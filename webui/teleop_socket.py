"""Small bounded RFC6455 text channel for local hold-to-drive controls.

No fragmented messages/extensions/binary data: control messages are <1KB.
Server challenges expire after 200ms, so a delayed buffered keepalive cannot
resume motion. Each socket has one held press; socket loss releases it.
"""
import base64
import hashlib
import json
import secrets
import socket
import struct
import time


def read_frame(stream):
    header = stream.read(2)
    if len(header) != 2:
        raise EOFError('WebSocket closed')
    a, b = header
    opcode, size = a & 15, b & 127
    if size == 127: raise ValueError('Control frame too large')
    if a & 0x70 or not a & 0x80 or not b & 0x80 or opcode not in (1, 8, 9, 10):
        raise ValueError('Unsupported or unmasked WebSocket frame')
    if size == 126:
        extended = stream.read(2)
        if len(extended) != 2: raise EOFError()
        size = struct.unpack('!H', extended)[0]
    if size > 1024 or (opcode >= 8 and size > 125):
        raise ValueError('Control frame too large')
    mask = stream.read(4)
    data = stream.read(size)
    if len(mask) != 4 or len(data) != size: raise EOFError()
    return opcode, bytes(v ^ mask[i % 4] for i, v in enumerate(data))


def write_frame(stream, data, opcode=1):
    if isinstance(data, dict): data = json.dumps(data, ensure_ascii=False).encode('utf-8')
    stream.write(bytes([0x80 | opcode, len(data)]) if len(data) < 126 else bytes([0x80 | opcode, 126]) + struct.pack('!H', len(data)))
    stream.write(data)
    stream.flush()


def serve(handler):
    desk = handler.server.controller
    key = handler.headers.get('Sec-WebSocket-Key', '')
    try:
        if len(base64.b64decode(key, validate=True)) != 16: raise ValueError()
    except ValueError:
        handler._json({'error': 'Invalid WebSocket key'}, 400); return
    if (handler.headers.get('Upgrade', '').lower() != 'websocket' or
            'upgrade' not in handler.headers.get('Connection', '').lower() or
            handler.headers.get('Sec-WebSocket-Version') != '13' or not handler.headers.get('Origin')):
        handler._json({'error': 'Invalid WebSocket handshake'}, 400); return
    handler.send_response(101)
    handler.send_header('Upgrade', 'websocket')
    handler.send_header('Connection', 'Upgrade')
    accept = base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode('ascii')).digest()).decode('ascii')
    handler.send_header('Sec-WebSocket-Accept', accept)
    handler.end_headers()
    handler.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    handler.connection.settimeout(3)
    owner = None
    seq = 0
    challenge, issued = None, 0
    holding = False
    try:
        while not desk.finished.is_set():
            opcode, data = read_frame(handler.rfile)
            if opcode == 8: break
            if opcode == 9:
                write_frame(handler.wfile, data, 10); continue
            if opcode == 10: continue
            processing_at = time.perf_counter()
            control_stop = None
            message = json.loads(data.decode('utf-8'))
            if not isinstance(message, dict): raise ValueError('JSON object required')
            if owner is None:
                token = message.get('token')
                if not isinstance(token, str) or not secrets.compare_digest(token, handler.server.token):
                    raise ValueError('Invalid control token')
                owner = message.get('client')
                # Auth does not seize device ownership or enable movement.
                if not isinstance(owner, str) or not 8 <= len(owner) <= 80: raise ValueError('Invalid client')
            else:
                if type(message.get('seq')) is not int or message['seq'] != seq+1:
                    raise ValueError('Stale/out-of-order control message')
                seq = message['seq']
                action = message.get('action')
                # Release always works, even when its challenge has expired.
                if action == 'release':
                    desk.teleop_release(owner); holding = False
                    handler.connection.settimeout(3)
                else:
                    if message.get('challenge') != challenge or time.monotonic()-issued > .2:
                        raise ValueError('Expired control challenge; release and press again')
                    if action == 'start' and not holding:
                        desk.teleop_start(owner, message.get('direction'),pwm_percent=message.get('pwm_percent')); holding = True
                        handler.connection.settimeout(.2)
                    elif action == 'pulse' and holding:
                        result=desk.teleop_pulse(owner,pwm_percent=message.get('pwm_percent'))
                        if result.get('holding') is False:
                            holding=False;control_stop=result.get('control_stop')
                            handler.connection.settimeout(3)
                    elif action != 'ping':
                        raise ValueError('Invalid hold state')
            challenge = secrets.token_urlsafe(12)
            issued = time.monotonic()
            write_frame(handler.wfile, {'seq': seq, 'challenge': challenge, 'holding': holding,
                                        'server_processing_ms': (time.perf_counter()-processing_at)*1000,
                                        'control_stop': control_stop,
                                        'lease_ms': 250, 'firmware': (desk.backend.snapshot() if desk.backend else {}).get('firmware')})
    except Exception as exc:
        # A socket failure must never escape without independently requesting STOP.
        try: write_frame(handler.wfile, {'error': desk.scrub(exc)})
        except OSError: pass
    finally:
        if holding and owner: desk.teleop_release(owner, reason='遥控连接断开')
        handler.close_connection = True
