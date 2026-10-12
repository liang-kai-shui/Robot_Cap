"""Loopback HTTP + server-sent events. No shell or serial access in the browser."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import socket
from urllib.parse import urlparse, parse_qs


STATIC = Path(__file__).resolve().parent/'static'


class DeskServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, controller):
        if address[0] != '127.0.0.1':
            raise ValueError('WebUI只监听本机127.0.0.1')
        self.controller = controller
        self.token = secrets.token_urlsafe(32)
        super().__init__(address, DeskHandler)


class DeskHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def log_message(self, *_args):
        pass  # Never write request bodies, API keys, or query contents to stdout.

    def _local(self):
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            self.close_connection = True
            self._json({'error': '仅允许本机控制台访问'}, 403)
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.close_connection = True
            self._json({'error': '拒绝跨站访问'}, 403)
            return False
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{host}' for host in hosts}:
            self.close_connection = True
            self._json({'error': '页面来源不匹配'}, 403)
            return False
        return True

    def _headers(self, content_type, size=None):
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        if size is not None:
            self.send_header('Content-Length', str(size))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()

    def _json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_response(status)
        self._headers('application/json; charset=utf-8', len(payload))
        self.wfile.write(payload)

    def do_GET(self):
        if not self._local():
            return
        path = urlparse(self.path).path
        controller = self.server.controller
        try:
            if path == '/api/teleop':
                from webui.teleop_socket import serve
                serve(self)
            elif path == '/api/health':
                self._json({'service': 'robot-cap-webui', 'version': 1})
            elif path == '/api/bootstrap':
                self._json({'token': self.server.token, 'state': controller.snapshot()})
            elif path == '/api/state':
                self._json(controller.snapshot())
            elif path == '/api/ports':
                self._json({'ports': controller.ports()})
            elif path == '/api/logs':
                self._json({'state': controller.snapshot(), 'events': controller.event_batch(0)})
            elif path == '/api/events':
                self._events(controller)
            elif path == '/api/document':
                name = parse_qs(urlparse(self.path).query).get('name', ['webui'])[0]
                files = {'webui': 'WEBUI.md', 'hardware': 'HARDWARE_INTERFACES_GUIDE.md',
                         'protocol': 'SERIAL_PROTOCOL.md'}
                if name not in files:
                    raise ValueError('文档不存在')
                file = Path(__file__).resolve().parents[1]/'docs'/files[name]
                self._json({'name': files[name], 'text': file.read_text(encoding='utf-8')})
            elif path in ('/', '/index.html', '/app.js', '/styles.css', '/visuals.js', '/attitude-math.js',
                          '/vendor/three/three.module.js', '/vendor/three/three.core.js',
                          '/vendor/three/OrbitControls.js'):
                file = STATIC/('index.html' if path == '/' else path[1:])
                payload = file.read_bytes()
                types = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8'}
                self.send_response(200)
                self._headers(types[file.suffix], len(payload))
                self.wfile.write(payload)
            else:
                self._json({'error': '页面不存在'}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self._json({'error': controller.scrub(exc)}, 400)

    def _events(self, controller):
        self.send_response(200)
        self._headers('text/event-stream; charset=utf-8')
        self.connection.settimeout(3)
        cursor = 0
        try:
            while not controller.finished.is_set():
                events = controller.event_batch(cursor)
                if events:
                    cursor = events[-1]['id']
                data = {'state': controller.snapshot(), 'events': events}
                payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
                self.wfile.write(f'data: {payload}\n\n'.encode('utf-8'))
                self.wfile.flush()
                controller.finished.wait(.1)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        self.close_connection = True

    def do_POST(self):
        if not self._local():
            return
        if not secrets.compare_digest(self.headers.get('X-Rover-Token', ''), self.server.token):
            # A stale page can post an old token after a service restart. Its
            # unread body must not become the next request on this connection.
            self.close_connection = True
            self._json({'error': '控制令牌无效，请刷新页面'}, 403)
            return
        controller = self.server.controller
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 16384:
                self.close_connection = True
                raise ValueError('请求大小无效')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('请求必须是JSON对象')
            owner = body.get('client')
            path = urlparse(self.path).path
            if path == '/api/heartbeat':
                controller.heartbeat(owner)
                result = {'alive': True}
            elif path == '/api/connect': result = controller.connect(owner, body.get('port'))
            elif path == '/api/disconnect': result = controller.disconnect(owner)
            elif path == '/api/stop': result = controller.stop('网页停止')
            elif path == '/api/manual': result = controller.manual(owner, body.get('action'), body.get('value'))
            elif path == '/api/generate': result = controller.generate(owner, body.get('instruction'), body.get('auto_execute', False))
            elif path == '/api/range-offset': result = controller.set_range_offset(owner, body.get('offset_mm'))
            elif path == '/api/range-calibrate': result = controller.calibrate_range(owner, body.get('known_mm'))
            elif path == '/api/execute': result = controller.execute(owner, body.get('plan_id'))
            elif path == '/api/settings': result = controller.configure(owner, body)
            elif path == '/api/terminal': result = controller.terminal(owner, body.get('command'))
            elif path == '/api/build': result = controller.build(owner)
            elif path == '/api/flash': result = controller.build(owner, flash_port=body.get('port'), motor_power_disconnected=body.get('motor_power_disconnected'))
            elif path == '/api/shutdown':
                controller.stop('结束本地服务')
                result = {'shutting_down': True}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self._json({'error': '接口不存在'}, 404)
                return
            self._json(result)
        except Exception as exc:
            self._json({'error': controller.scrub(f'{type(exc).__name__}: {exc}')}, 400)

