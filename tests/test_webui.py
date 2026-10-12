"""Offline browser service tests with fake serial; never open a hardware port."""
from contextlib import contextmanager
import json
import http.client
import threading
import time
import urllib.error
import urllib.request

import pytest

from robot.backends.esp32_serial import ESP32SerialBackend
from runtime.errors import RobotEmergencyStopError
from webui.controller import ControlDesk
from webui.demo import DemoBackend, DemoProvider
from webui.server import DeskServer
from test_esp32_serial import FakePort, F_START
from test_physical_session import ScriptedModel
from providers.base import LLMResponse


CLIENT = 'test-browser-one'
FOUR = 'robot.move(.1)\nrobot.turn(30)\nrobot.turn(-30)\nrobot.move(.1)\nrobot.stop()'


def wait_job(desk, timeout=4):
    end = time.monotonic()+timeout
    while desk.snapshot()['job']['status'] == 'running' and time.monotonic() < end:
        desk.heartbeat(CLIENT)
        time.sleep(.01)
    assert desk.snapshot()['job']['status'] != 'running'
    return desk.snapshot()['job']


@contextmanager
def fake_desk(tmp_path, responses=(), port=None):
    port = port or FakePort()
    model = ScriptedModel(responses)
    desk = ControlDesk(backend_factory=lambda name: ESP32SerialBackend(name, serial_factory=lambda: port),
                       provider_factory=lambda: model, log_dir=tmp_path, monitor=False)
    try:
        yield desk, port, model
    finally:
        desk.close()


def connect(desk):
    desk.connect(CLIENT, 'COM5')
    assert wait_job(desk)['status'] == 'completed'


def test_starts_disconnected_no_model_or_serial_access(tmp_path):
    with fake_desk(tmp_path) as (desk, port, model):
        assert not desk.snapshot()['device']['connected']
        assert port.sent == [] and not model.requests
        assert desk.snapshot()['telemetry']['yaw'] is None


def test_connection_and_manual_action_use_original_worker_and_stopped_feedback(tmp_path):
    with fake_desk(tmp_path) as (desk, port, model):
        connect(desk)
        desk.manual(CLIENT, 'move', .1)
        job = wait_job(desk)
        assert job['status'] == 'completed' and job['result']['task_success'] is None
        assert port.sent == ['t', '@F100\n', 't']
        assert desk.snapshot()['device']['stopped'] is True
        assert not model.requests and desk.tasks[-1]['execution_success']


@pytest.mark.parametrize('action,value', [('move', -.1), ('move', .5), ('move', True),
    ('move', float('nan')), ('turn', 180), ('turn', 10), ('turn', 30.05), ('reverse', .1)])
def test_invalid_manual_parameters_never_move(tmp_path, action, value):
    with fake_desk(tmp_path) as (desk, port, _):
        connect(desk)
        with pytest.raises(Exception):
            desk.manual(CLIENT, action, value)
        assert port.sent == ['t']


def test_model_plan_is_preview_only_then_two_segments_use_actual_review(tmp_path):
    with fake_desk(tmp_path, [FOUR, 'CONTINUE']) as (desk, port, model):
        connect(desk)
        desk.generate(CLIENT, '前进10厘米，左转30度，右转30度，再前进10厘米，停止')
        assert wait_job(desk)['status'] == 'completed' and port.sent == ['t']
        plan = desk.snapshot()['plan']
        assert len(plan['segments']) == 2
        desk.execute(CLIENT, plan['id'])
        job = wait_job(desk)
        assert job['status'] == 'completed' and job['result']['completed_motion_calls'] == 4
        assert [w for w in port.sent if w != 't'] == ['@F100\n', '@J300\n', '@L300\n', '@F100\n']
        assert len(model.requests)==1
        assert desk.tasks[-1]['reviews'][0]['source']=='local_validated_feedback'
        assert desk.snapshot()['plan'] is None
        with pytest.raises(ValueError):
            desk.execute(CLIENT, plan['id'])


def test_plan_invalidated_by_new_manual_action(tmp_path):
    with fake_desk(tmp_path, ['robot.turn(30)\nrobot.stop()']) as (desk, port, _):
        connect(desk); desk.generate(CLIENT, '左转30度'); wait_job(desk)
        identifier = desk.snapshot()['plan']['id']
        desk.manual(CLIENT, 'move', .1); wait_job(desk)
        with pytest.raises(ValueError): desk.execute(CLIENT, identifier)
        assert '@J300\n' not in port.sent


def test_whole_invalid_plan_is_rejected_without_motion(tmp_path):
    with fake_desk(tmp_path, [FOUR.replace('robot.move(.1)\nrobot.stop()', 'robot.turn(180)\nrobot.stop()')]) as (desk, port, _):
        connect(desk); desk.generate(CLIENT, '任务')
        assert wait_job(desk)['status'] == 'failed' and port.sent == ['t']
        assert desk.snapshot()['plan'] is None


@pytest.mark.parametrize('reply', ['ABORT: 反馈不确定', 'CONTINUE\nrobot.move(.3)', 'robot.turn(90)'])
def test_feedback_cannot_add_actions_or_resume_after_abort(tmp_path, reply):
    with fake_desk(tmp_path, [FOUR, reply]) as (desk, port, _):
        desk.settings['review_mode']='model'
        connect(desk); desk.generate(CLIENT, '任务'); wait_job(desk)
        desk.execute(CLIENT, desk.snapshot()['plan']['id'])
        assert wait_job(desk)['status'] == 'failed'
        assert [w for w in port.sent if w != 't'] == ['@F100\n', '@J300\n', '@L300\n']
        assert desk.snapshot()['device']['fault']


def test_emergency_stop_during_active_motion_is_independent_and_no_following_turn(tmp_path):
    port = FakePort(responses={'f': F_START})
    with fake_desk(tmp_path, ['robot.move(.3)\nrobot.turn(30)\nrobot.stop()'], port=port) as (desk, port, _):
        connect(desk); desk.generate(CLIENT, '任务'); wait_job(desk)
        desk.execute(CLIENT, desk.snapshot()['plan']['id'])
        with desk.backend._condition:
            assert desk.backend._condition.wait_for(lambda: desk.backend._active and desk.backend._active.started, timeout=2)
        started = time.monotonic(); desk.stop('测试停止')
        assert time.monotonic()-started < .5
        assert wait_job(desk)['status'] == 'cancelled'
        assert [w for w in port.sent if w != 't'] == ['f']


def test_controller_does_not_queue_manual_calls(tmp_path):
    port = FakePort(responses={'f': F_START})
    with fake_desk(tmp_path, port=port) as (desk, port, _):
        connect(desk); desk.manual(CLIENT, 'move', .3)
        with pytest.raises(Exception): desk.manual(CLIENT, 'turn', 30)
        desk.stop(); wait_job(desk)
        assert '@J300\n' not in port.sent


def test_cancelled_slow_generation_cannot_publish_a_late_plan(tmp_path):
    ready, release = threading.Event(), threading.Event()
    class Slow:
        def generate_policy(self, *_args):
            ready.set(); release.wait(2)
            return LLMResponse('robot.move(.1)\nrobot.stop()', 'fake', None, None, None, 1)
    desk = ControlDesk(demo=True, provider_factory=Slow, log_dir=tmp_path, monitor=False)
    try:
        desk.generate(CLIENT, '任务'); assert ready.wait(1)
        desk.stop(); assert wait_job(desk)['status'] == 'cancelled'
        release.set(); time.sleep(.05)
        assert desk.snapshot()['plan'] is None and desk.backend is None
    finally:
        release.set(); desk.close()


def test_only_owner_can_control_and_heartbeat_loss_cancels_job(tmp_path):
    port = FakePort(responses={'f': F_START})
    with fake_desk(tmp_path, port=port) as (desk, port, _):
        connect(desk)
        with pytest.raises(ValueError): desk.manual('another-browser', 'move', .1)
        desk.manual(CLIENT, 'move', .3)
        desk.last_heartbeat = time.monotonic()-10
        desk.heartbeat('another-browser')
        desk.poll()
        assert wait_job(desk)['status'] == 'cancelled'
        assert desk.snapshot()['device']['fault']


def test_telemetry_preserves_firmware_invalidity_and_equal_time_records(tmp_path):
    desk = ControlDesk(demo=True, log_dir=tmp_path, monitor=False)
    backend = DemoBackend(); desk.backend = backend
    try:
        backend._logs.append({'kind':'rx','time_monotonic':1, 'line':'BMI ready=1 calibrated=1 fresh=0 error=1 roll=2.0 pitch=1.0'})
        backend._logs.append({'kind':'rx','time_monotonic':1, 'line':'BODY_YAW angle_deg=22 valid=1 rate_dps=0.0'})
        desk.poll()
        assert desk.snapshot()['telemetry']['bmi']['fresh'] is False
        backend._logs.append({'kind':'rx','time_monotonic':1, 'line':'BODY_YAW angle_deg=23 valid=1 rate_dps=0.0'})
        desk.poll()
        assert desk.snapshot()['telemetry']['yaw']['angle_deg'] == 23
        assert len(desk.snapshot()['telemetry']['samples']) == 2
    finally: desk.close()


def test_key_never_echoes_in_snapshot_events_or_saved_record(tmp_path):
    desk = ControlDesk(demo=True, log_dir=tmp_path, monitor=False)
    key = 'test-secret-with-quote"and-slash\\'
    try:
        desk.configure(CLIENT, {'api_key':key, 'model':'demo'})
        desk.emit('system','error',key)
        desk.save()
        combined = json.dumps(desk.snapshot())+json.dumps(desk.event_batch(0))+(tmp_path/'latest-session.json').read_text(encoding='utf-8')
        assert key not in combined and json.dumps(key)[1:-1] not in combined
        assert desk.snapshot()['settings']['key_configured']
    finally: desk.close()


def test_terminal_is_real_robot_commands_and_never_an_os_shell(tmp_path, monkeypatch):
    desk = ControlDesk(demo=True, log_dir=tmp_path, monitor=False)
    monkeypatch.setattr('subprocess.Popen', lambda *_a, **_kw: pytest.fail('Unexpected OS shell'))
    try:
        assert '命令' in desk.terminal(CLIENT, 'help')['text']
        for command in ('python -c print(1)', 'powershell', 'rm -rf /', '__import__("os")'):
            with pytest.raises(ValueError): desk.terminal(CLIENT, command)
        desk.terminal(CLIENT, 'connect DEMO'); wait_job(desk)
        desk.terminal(CLIENT, 'turn 30'); assert wait_job(desk)['status'] == 'completed'
    finally: desk.close()


@contextmanager
def http_desk(tmp_path):
    desk = ControlDesk(demo=True, log_dir=tmp_path, monitor=False)
    server = DeskServer(('127.0.0.1', 0), desk)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try: yield desk, server, base
    finally:
        server.shutdown(); desk.close(); server.server_close(); thread.join(1)


def http_json(base, path, data=None, headers=None):
    req = urllib.request.Request(base+path, data=None if data is None else json.dumps(data).encode(),
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        error.close()  # Expected rejection responses also own a socket.
        raise


def test_http_static_api_bootstrap_and_csrf_rejection(tmp_path):
    with http_desk(tmp_path) as (desk, server, base):
        assert http_json(base, '/api/health')['service'] == 'robot-cap-webui'
        initial = http_json(base, '/api/bootstrap')
        assert not initial['state']['device']['connected']
        with pytest.raises(urllib.error.HTTPError) as error:
            http_json(base, '/api/manual', {'client':CLIENT,'action':'move','value':.1})
        assert error.value.code == 403
        headers = {'X-Rover-Token':initial['token'], 'Content-Type':'application/json'}
        http_json(base, '/api/connect', {'client':CLIENT,'port':'DEMO'}, headers)
        assert wait_job(desk)['status'] == 'completed'
        with urllib.request.urlopen(base+'/') as response:
            assert '自然语言任务' in response.read().decode()
            assert response.headers['X-Frame-Options'] == 'DENY'
        for path in ('/visuals.js', '/vendor/three/three.module.js', '/vendor/three/three.core.js', '/vendor/three/OrbitControls.js'):
            with urllib.request.urlopen(base+path) as response:
                assert response.status == 200 and 'javascript' in response.headers['Content-Type']
        with pytest.raises(urllib.error.HTTPError) as error:
            http_json(base, '/api/stop', {'client':CLIENT}, {**headers, 'Origin':'https://example.com'})
        assert error.value.code == 403


def test_http_rejects_path_traversal_and_cross_site_host(tmp_path):
    with http_desk(tmp_path) as (_, server, base):
        for path in ('/../.env', '/api/document?name=../../.env'):
            with pytest.raises(urllib.error.HTTPError): http_json(base,path)
        with pytest.raises(urllib.error.HTTPError) as error:
            http_json(base, '/api/bootstrap', headers={'Host':'attacker.invalid'})
        assert error.value.code == 403


def test_demo_generation_and_model_review_are_explicitly_offline():
    provider = DemoProvider()
    response = provider.generate_policy('前进10厘米左转30度', '', '{}')
    assert '未调用模型' in response.model and 'robot.move(0.1)' in response.text
    assert provider.generate_policy('任务','',json.dumps({'completed_segment':{}})).text == 'CONTINUE'


def test_last_move_feedback_survives_a_subsequent_turn(tmp_path):
    desk = ControlDesk(demo=True, log_dir=tmp_path, monitor=False)
    try:
        desk.connect(CLIENT, 'DEMO'); wait_job(desk)
        desk.manual(CLIENT, 'move', .1); wait_job(desk); desk.poll()
        move = desk.snapshot()['telemetry']['last_move']
        desk.manual(CLIENT, 'turn', 30); wait_job(desk); desk.poll()
        assert desk.snapshot()['device']['last_result']['target_deg'] == 30
        assert desk.snapshot()['telemetry']['last_move'] == move
        assert move['after_stop_mean_mm'] == 111.1
    finally:
        desk.close()


def test_stale_token_does_not_corrupt_the_next_http_request(tmp_path):
    with http_desk(tmp_path) as (_, server, _):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=3)
        try:
            connection.request('POST', '/api/heartbeat', body=json.dumps({'client':CLIENT}),
                               headers={'X-Rover-Token':'expired'})
            rejected = connection.getresponse()
            assert rejected.status == 403 and rejected.getheader('Connection') == 'close'
            rejected.read()
            connection.request('GET', '/api/health')
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())['service'] == 'robot-cap-webui'
        finally:
            connection.close()


@pytest.mark.parametrize('fields,expected', [
    ('ready=1 distance_mm=423 valid=1 status=0 error=0 age_ms=12', True),
    ('ready=1 distance_mm=423 valid=0 status=4 error=0 age_ms=12', False),
    ('ready=1 distance_mm=423 valid=1 status=0 error=-2 age_ms=12', False),
    ('ready=1 distance_mm=423 valid=1 status=0 error=0 age_ms=201', False),
    ('ready=1 distance_mm=423 valid=1 status=0 error=0 age_ms=-1', False),
    ('ready=0 distance_mm=0 valid=0 status=0 error=-1 age_ms=-1', False),
])
def test_range_validity_includes_sensor_age_optical_status_and_bus_error(tmp_path, fields, expected):
    desk = ControlDesk(demo=True, log_dir=tmp_path, monitor=False)
    try:
        desk._receive({'kind':'rx', 'line':'TOF '+fields})
        telemetry = desk.snapshot()['telemetry']
        assert telemetry['tof_disabled'] is False
        assert telemetry['range']['fresh'] is expected
        assert telemetry['range']['sensor_age_ms'] == float(fields.rsplit('=',1)[1])
        desk._receive({'kind':'rx','line':'TOF DISABLED front_guard=OFF'})
        assert desk.snapshot()['telemetry']['range'] is None
    finally: desk.close()


def test_live_wheels_and_attitude_preserve_invalid_and_stale_data(tmp_path):
    desk = ControlDesk(demo=True, log_dir=tmp_path, monitor=False)
    try:
        desk._receive({'kind':'rx','line':'WHEEL motor=A side=RIGHT forward_count=1234 cps=1919 speed_mm_s=100 rpm=44.42 valid=1 dt_ms=250 age_ms=0'})
        desk._receive({'kind':'rx','line':'ATTITUDE roll=2 pitch=-3 yaw=10 tilt_valid=1 yaw_valid=1 calibrated=1 age_ms=4 zero_epoch=0'})
        view=desk.snapshot()['telemetry']
        assert view['wheels']['A']['fresh'] and view['wheels']['A']['speed_mm_s'] == 100
        assert view['attitude']['yaw_valid'] == 1 and view['yaw']['valid'] == 1
        desk._receive({'kind':'rx','line':'ATTITUDE roll=2 pitch=-3 yaw=11 tilt_valid=1 yaw_valid=0 calibrated=1 age_ms=4 zero_epoch=0'})
        assert desk.snapshot()['telemetry']['yaw']['valid'] == 0
        desk.telemetry['wheels']['A']['_at'] -= 2
        assert desk.snapshot()['telemetry']['wheels']['A']['fresh'] is False
    finally: desk.close()


def test_manual_reverse_stays_out_of_model_capabilities_and_invalidates_turn_reference(tmp_path):
    from test_esp32_serial import REVERSE
    from agent.physical_context import build_physical_history
    with fake_desk(tmp_path, responses=['robot.turn(30)\nrobot.stop()'], port=FakePort(responses={'s':REVERSE})) as (desk, port, model):
        connect(desk)
        desk.manual(CLIENT,'turn',30); wait_job(desk)
        assert build_physical_history(list(desk.tasks))['turn_back_reference']
        desk.manual(CLIENT,'reverse',.8)
        job=wait_job(desk)
        assert job['status']=='completed' and job['result']['manual_reverse']
        assert port.sent.count('s')==1 and desk.snapshot()['device']['stopped']
        assert build_physical_history(list(desk.tasks))['turn_back_reference'] is None
        assert not model.requests


def test_stop_during_manual_reverse_is_independent_and_cancels(tmp_path):
    from test_esp32_serial import REVERSE_START
    port=FakePort(responses={'s':REVERSE_START})
    with fake_desk(tmp_path,port=port) as (desk,port,_):
        connect(desk); desk.manual(CLIENT,'reverse',.8)
        with desk.backend._condition:
            assert desk.backend._condition.wait_for(lambda:desk.backend._active and desk.backend._active.started,timeout=2)
        desk.stop()
        assert wait_job(desk)['status']=='cancelled'
        assert port.sent.count('s')==1 and port.sent[-1]=='t'
