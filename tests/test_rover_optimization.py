"""New rover features: fake serial/model/socket only; no real motor commands."""
import io
import json
import socket
import struct
import threading
import time

import pytest
from agent.physical_context import build_physical_history
from agent.physical_policy import validate_physical_policy
from agent.prompts import generate_robot_api_prompt
from providers.base import LLMResponse
from providers.openai_compatible import OpenAICompatibleProvider
from robot.backends.esp32_serial import ESP32SerialBackend, build_esp32_registry
from runtime.errors import RobotActionError, RobotBackendError
from webui.controller import ControlDesk
from webui.demo import DemoBackend
from webui.server import DeskServer
from webui.teleop_socket import read_frame
from test_esp32_serial import FakePort, ACK, READY, transcript
from test_webui import CLIENT, wait_job, connect, FOUR


READY2=READY.replace('firmware=1.8 protocol=PARAM1','firmware=1.9 protocol=PARAM2 boot_id=abcdef01 teleop_next=1 tof=ON')
RANGE='TOF ready=1 distance_mm=600 valid=1 status=0 error=0 age_ms=0\n'
APPROACH=('VEHICLE_START action=r target_mm=300\nVEHICLE_STOP action=r source=RANGE_TARGET\n'
          'RANGE_RESULT action=r target_mm=300 cutoff_mm=312 after_stop_mm=302 valid=1 elapsed_ms=2700\n'
          'VEHICLE_DONE action=r type=RANGE_APPROACH\n'+READY2)


def hardware(responses=None):
    port=FakePort(handshake=ACK+READY2,responses=responses,chunks=True)
    backend=ESP32SerialBackend(serial_factory=lambda:port)
    backend.connect(timeout=.5)
    return backend,port


def test_reverse_distance_requires_signed_action_and_complete_feedback():
    reply=transcript('@F100\n').replace('action=f','action=b').replace(READY,READY2)
    backend,port=hardware({'@B100\n':reply})
    try:
        result=backend.reverse_distance(.1)
        assert result['encoder_valid'] and result['yaw_valid'] and result['action']=='b'
        assert port.sent==['t','@B100\n']
    finally: backend.close()


def test_approach_uses_live_range_and_firmware_range_result():
    backend,port=hardware({'@R300\n':APPROACH})
    try:
        backend._on_line(RANGE)
        result=backend.approach(.3)
        assert result['valid'] and result['corrected_after_stop_mm']==302
        assert port.sent==['t','@R300\n']
    finally: backend.close()


@pytest.mark.parametrize('reading',[RANGE.replace('600','250'),RANGE.replace('valid=1','valid=0'),RANGE.replace('age_ms=0','age_ms=201')])
def test_approach_never_moves_with_close_invalid_stale_range(reading):
    backend,port=hardware()
    try:
        backend._on_line(reading)
        with pytest.raises(RobotActionError): backend.approach(.3)
        assert port.sent==['t']
    finally: backend.close()


def test_offset_shared_by_observation_and_approach_wire_target():
    reply=APPROACH.replace('target_mm=300','target_mm=280').replace('after_stop_mm=302','after_stop_mm=282')
    backend,port=hardware({'@R280\n':reply})
    try:
        backend.configure_range_offset(20);backend._on_line(RANGE)
        assert backend.get_distance()['distance_mm']==620
        assert backend.approach(.3)['corrected_after_stop_mm']==302
        assert '@R280\n' in port.sent
    finally: backend.close()


def test_teleop_does_not_use_worker_or_queue_and_release_writes_stop():
    reply='VEHICLE_START action=w\nTELEOP_START session=1 direction=1\nTELEOP_ACK session=1 direction=1\n'
    backend,port=hardware({'@H11\n':reply})
    try:
        assert backend.teleop_start('forward')['session']==1
        with pytest.raises(RobotActionError): backend.teleop_start('backward')
        backend.teleop_release()
        with backend._condition: assert backend._condition.wait_for(lambda:backend._ready,timeout=.5)
        assert port.sent==['t','@H11\n','t']
        assert backend.snapshot()['stopped'] and not backend.snapshot()['teleop']
    finally: backend.close()


@pytest.mark.parametrize('source',[APPROACH.replace('valid=1','valid=0'),APPROACH.replace('after_stop_mm=302','after_stop_mm=600'),APPROACH.replace('RANGE_TARGET','DISTANCE_TARGET')])
def test_invalid_range_completion_aborts_and_stops(source):
    backend,port=hardware({'@R300\n':source})
    try:
        backend._on_line(RANGE)
        with pytest.raises(Exception):backend.approach(.3)
        assert port.sent[-1]=='t' and backend.snapshot()['fault']
    finally:backend.close()


def test_new_apis_validate_whole_plan_without_allowing_arbitrary_code():
    registry=build_esp32_registry(ESP32SerialBackend())
    validate_physical_policy('robot.reverse(.1)\nrobot.get_distance()\nrobot.stop()',registry)
    validate_physical_policy('robot.approach(.3)\nrobot.get_attitude()\nrobot.stop()',registry)
    for policy in ['robot.approach(.03)\nrobot.stop()','robot.reverse(-.1)\nrobot.stop()',
                   'robot.goto(1,2)\nrobot.stop()','robot.teleop_start("forward")\nrobot.stop()']:
        with pytest.raises(Exception):validate_physical_policy(policy,registry)


def test_full_session_history_includes_earlier_tasks_without_raw_secrets():
    tasks=[{'number':n,'instruction':f'task {n}','trace':[],'error':'rejected','api_key':'SECRET'} for n in range(100)]
    result=build_physical_history(tasks)
    assert result['total_tasks']==100 and len(result['earlier_tasks'])==96
    assert result['earlier_tasks'][0]['task_number']==0 and 'SECRET' not in json.dumps(result)


def test_same_boot_reconnect_retains_context_restart_clears(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        desk.connect(CLIENT,'DEMO');wait_job(desk)
        desk.tasks.append({'number':1,'instruction':'remember','trace':[]})
        session=desk.session_id
        desk.disconnect(CLIENT);desk.connect(CLIENT,'DEMO');wait_job(desk)
        assert desk.session_id==session and desk.tasks[0]['instruction']=='remember'
        desk._receive({'kind':'rx','line':'KEYBOARD_DRIVE v1.9','time_monotonic':0})
        assert not desk.tasks and desk.session_id!=session
    finally:desk.close()


def test_auto_execute_is_opt_in_and_uses_validated_plan(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        desk.connect(CLIENT,'DEMO');wait_job(desk)
        desk.generate(CLIENT,'后退10厘米，停止');wait_job(desk)
        assert not any(e['kind']=='tx' and e.get('command')=='f' for e in desk.backend.logs)
        desk.generate(CLIENT,'后退10厘米，停止',auto_execute=True)
        job=wait_job(desk)
        assert job['status']=='completed' and job['result']['execution_success'] and job['policy'].startswith('robot.reverse')
    finally:desk.close()


def test_model_validation_error_retains_separate_diagnostic_no_motion(tmp_path):
    class Model:
        def generate_policy(self,*a):return LLMResponse('robot.goto(1,2)\nrobot.stop()','fake',0,0,None,0)
    desk=ControlDesk(demo=True,provider_factory=Model,log_dir=tmp_path,monitor=False)
    try:
        desk.generate(CLIENT,'navigate');job=wait_job(desk)
        assert job['status']=='failed' and job['policy'].startswith('robot.goto')
        error=desk.snapshot()['errors'][-1]
        assert error['category']=='validation' and 'robot.goto' in error['raw_response']
        assert desk.backend is None
    finally:desk.close()


def test_hold_watchdog_releases_and_blocks_other_tasks(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        desk.connect(CLIENT,'DEMO');wait_job(desk)
        desk.teleop_start(CLIENT,'forward')
        with pytest.raises(ValueError):desk.generate(CLIENT,'左转30度')
        desk.holding['_pulse_at']=time.monotonic()-.25
        desk.poll()
        assert desk.holding is None and desk.backend.stopped
    finally:desk.close()


def test_stable_api_prefix_preserves_cacheable_instructions(monkeypatch):
    payloads=[]
    def urlopen(request,timeout):
        payloads.append(json.loads(request.data))
        return io.BytesIO(b'{"choices":[{"message":{"content":"robot.stop()"}}]}')
    monkeypatch.setattr('urllib.request.urlopen',urlopen)
    provider=OpenAICompatibleProvider('https://example.test','KEY','model',stable_api_prefix=True,reasoning_effort='none')
    provider.generate_policy('one','API','state1','SYSTEM');provider.generate_policy('two','API','state2','SYSTEM')
    assert payloads[0]['messages'][0]==payloads[1]['messages'][0]
    assert payloads[0]['reasoning_effort']=='none' and 'KEY' not in str(payloads)


@pytest.mark.parametrize('frame',[b'\x01\x80',b'\x81\x00',b'\x82\x80',b'\x81\xff'])
def test_websocket_rejects_unmasked_fragmented_binary_oversize(frame):
    with pytest.raises((ValueError,EOFError)):read_frame(io.BytesIO(frame))


@pytest.mark.parametrize('pwm',[None,40])
def test_real_websocket_hold_disconnect_and_expired_challenge_stop(tmp_path,pwm):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    desk.connect(CLIENT,'DEMO');wait_job(desk)
    server=DeskServer(('127.0.0.1',0),desk)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    port=server.server_address[1]
    def open_channel():
        sock=socket.create_connection(('127.0.0.1',port),timeout=1)
        stream=sock.makefile('rwb',buffering=0)
        stream.write((f'GET /api/teleop HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:{port}\r\n'
                      'Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n'
                      'Sec-WebSocket-Key: AAAAAAAAAAAAAAAAAAAAAA==\r\n\r\n').encode())
        assert b'101' in stream.readline()
        while stream.readline()!=b'\r\n':pass
        return sock,stream
    def send(stream,message):
        body=json.dumps(message).encode();mask=b'abcd'
        stream.write(bytes([0x81,0x80|len(body)]) if len(body)<126 else b'\x81\xfe'+struct.pack('!H',len(body)))
        stream.write(mask+bytes(v^mask[i%4] for i,v in enumerate(body)))
        head=stream.read(2);size=head[1]&127
        if size==126:size=struct.unpack('!H',stream.read(2))[0]
        return json.loads(stream.read(size))
    sock=stream=None
    try:
        sock,stream=open_channel()
        ack=send(stream,{'client':CLIENT,'token':server.token})
        ack=send(stream,{'seq':1,'action':'start','direction':'forward','pwm_percent':pwm,'challenge':ack['challenge']})
        assert ack['holding'] and desk.holding
        for seq in range(2,5):
            ack=send(stream,{'seq':seq,'action':'pulse','pwm_percent':100 if pwm else None,'challenge':ack['challenge']})
        if pwm: assert desk.backend.snapshot()['manual_pwm_actual']=={'A':100,'B':100}
        stream.close();sock.close();stream=sock=None
        limit=time.monotonic()+1
        while desk.holding and time.monotonic()<limit:time.sleep(.01)
        assert not desk.holding and desk.backend.stopped
        sock,stream=open_channel()
        ack=send(stream,{'client':CLIENT,'token':server.token})
        time.sleep(.21)
        error=send(stream,{'seq':1,'action':'start','direction':'forward','challenge':ack['challenge']})
        assert 'Expired' in error['error'] and not desk.holding
    finally:
        if stream:stream.close()
        if sock:sock.close()
        server.shutdown();server.server_close();desk.close();worker.join(1)


def test_calibration_uses_stable_samples_persists_offset_not_key(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        desk.connect(CLIENT,'DEMO');wait_job(desk)
        desk.backend.range_mm=160
        desk.calibrate_range(CLIENT,140)
        result=wait_job(desk)['result']
        assert result['offset_mm']==-20 and desk.backend.get_distance()['distance_mm']==140
        record=json.loads((tmp_path/'range-calibration.json').read_text())
        assert record['offset_mm']==-20 and len(record['samples'])==20 and 'api_key' not in record
    finally:desk.close()


def test_flash_requires_explicit_motor_power_and_released_serial(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        with pytest.raises(ValueError):desk.build(CLIENT,flash_port='COM5')
        desk.connect(CLIENT,'DEMO');wait_job(desk)
        with pytest.raises(ValueError):desk.build(CLIENT,flash_port='COM5',motor_power_disconnected=True)
        assert not any(e['kind']=='tx' for e in desk.backend.logs)
    finally:desk.close()


def test_legacy_firmware_never_receives_new_motion_frames():
    port=FakePort();backend=ESP32SerialBackend(serial_factory=lambda:port);backend.connect(timeout=.5)
    try:
        for call in (lambda:backend.reverse_distance(.1),lambda:backend.approach(.3),lambda:backend.teleop_start('forward')):
            with pytest.raises(RobotActionError):call()
        assert port.sent==['t'] and 'robot.reverse(' not in generate_robot_api_prompt(build_esp32_registry(backend))
    finally:backend.close()


def test_stop_transport_error_still_finishes_failed_job(tmp_path):
    class BrokenStop(DemoBackend):
        def emergency_stop(self): raise OSError('serial disconnected')
    desk=ControlDesk(demo=True,backend_factory=BrokenStop,log_dir=tmp_path,monitor=False)
    try:
        def fail(job):
            desk.backend=BrokenStop();job['motion_attempted']=True
            raise RobotBackendError('feedback lost')
        desk._submit(CLIENT,'manual','unplug',fail)
        assert wait_job(desk)['status']=='failed'
        assert 'feedback lost' in desk.snapshot()['job']['error']
    finally:desk.backend=None;desk.close()
