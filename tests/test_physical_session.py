"""Interactive console lifecycle with fake model/serial and real Policy Worker."""
import json

import pytest

import physical_control
from providers.base import LLMResponse
from runtime.errors import PolicyGenerationError, RobotEmergencyStopError
from test_esp32_serial import FakePort, F_START, F_STOP, F_DONE, F_RESULT, READY, connected, transcript
from test_physical_control import install_fake_backend


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def generate_policy(self, task, api, state, system=None):
        self.requests.append((task, json.loads(state)))
        reply = next(self.responses)
        if isinstance(reply, BaseException):
            raise reply
        return LLMResponse(reply, 'deepseek-flash', 100, 20, None, 1)


def session(monkeypatch, tmp_path, instructions, responses, *, execute=True, port=None):
    port = port or FakePort()
    install_fake_backend(monkeypatch, port)
    model = ScriptedModel(responses)
    provider_calls = []
    def provider(args):
        provider_calls.append(1)
        return model
    monkeypatch.setattr(physical_control, '_model_provider', provider)
    lines = iter(instructions)
    monkeypatch.setattr(physical_control, '_read_instruction', lambda: next(lines))
    log = tmp_path/'session.json'
    args = ['--interactive', '--log', str(log)] + (['--execute'] if execute else [])
    result = physical_control.main(args)
    return result, port, model, provider_calls, json.loads(log.read_text(encoding='utf-8'))


def test_two_complete_tasks_share_model_and_single_serial_open(monkeypatch, tmp_path):
    port = FakePort()
    opens = []
    original = port.open
    def open_once():
        opens.append(1)
        original()
    port.open = open_once
    code, port, model, providers, log = session(monkeypatch, tmp_path,
        ['前进30厘米左转90度停止', '前进30厘米右转90度停止', 'q'],
        [physical_control.DEMO_POLICY, 'robot.move(.3)\nrobot.turn(-90)\nrobot.stop()'], port=port)
    assert code == 0 and len(providers) == 1 and len(opens) == 1
    assert port.sent == ['t', 't', 'f', 'j', 't', 't', 'f', 'l', 't', 't', 't']
    assert not port.is_open and len(model.requests) == 2
    assert all(item['execution_success'] for item in log['tasks'])
    assert all(item['last_feedback_state']['action_count'] == 3 for item in log['tasks'])
    assert model.requests[1][1]['feedback']['stopped'] is True
    assert model.requests[1][1]['serial_connected'] is True
    assert all(task['task_success'] is None for task in log['tasks'])


def test_preview_keeps_serial_closed_for_all_tasks(monkeypatch, tmp_path):
    result, port, model, providers, log = session(monkeypatch, tmp_path,
        ['前进30厘米停止', 'status', 't', '右转90度停止', 'q'],
        ['robot.move(.3)\nrobot.stop()', 'robot.turn(-90)\nrobot.stop()'], execute=False)
    assert result == 0 and port.sent == [] and not port.reset_called
    assert len(providers) == 1 and len(model.requests) == 2
    assert all(t['policy_validated'] and not t['execution_success'] for t in log['tasks'])
    assert not model.requests[0][1]['serial_connected']


@pytest.mark.parametrize('bad', [
    'robot.move(.3)\nrobot.turn(5)\nrobot.stop()',
    'REFUSE: 不支持后退',
    PolicyGenerationError('Model request failed'),
])
def test_generation_failure_can_accept_fresh_task_without_moving_bad_plan(monkeypatch, tmp_path, bad):
    result, port, model, providers, log = session(monkeypatch, tmp_path,
        ['不支持任务', '右转90度停止', 'q'], [bad, 'robot.turn(-90)\nrobot.stop()'])
    assert result == 0 and port.sent == ['t', 't', 'l', 't', 't', 't']
    assert log['tasks'][0]['error'] and not log['tasks'][0]['execution_success']
    assert log['tasks'][1]['execution_success'] and len(providers) == 1
    assert log['tasks'][0]['timings_ms']['model_request'] >= 0
    if isinstance(bad, str):
        assert log['tasks'][0]['timings_ms']['policy_validation'] >= 0


def test_variable_actions_and_timings_survive_worker_and_session_log(monkeypatch, tmp_path):
    result, port, model, providers, log = session(monkeypatch, tmp_path,
        ['前进20厘米左转45度停止', 'q'], ['robot.move(.2)\nrobot.turn(45)\nrobot.stop()'])
    assert result == 0 and port.sent == ['t', 't', '@F200\n', '@J450\n', 't', 't', 't']
    assert log['startup_timings_ms']['connection_handshake'] >= 0
    task = log['tasks'][0]
    assert set(task['timings_ms']) == {'policy_lookup','model_request', 'policy_validation', 'pre_task_standstill',
                                     'policy_execution', 'task_total'}
    assert all(value >= 0 for value in task['timings_ms'].values())
    assert task['timings_ms']['task_total'] >= sum(task['timings_ms'][key] for key in
                 ('model_request', 'policy_validation', 'pre_task_standstill', 'policy_execution'))
    actions = [item for item in task['trace'] if item['phase'] == 'completed']
    assert [item['result'].get('target_mm', item['result'].get('target_deg')) for item in actions] == [200, 45, None]
    assert all(item['result']['timings_ms']['action_total'] >= 0 for item in actions)


def test_three_motion_task_reserves_stop_and_next_task_gets_fresh_budget(monkeypatch, tmp_path):
    policy = 'robot.move(.1)\nrobot.turn(90)\nrobot.turn(90)\nrobot.stop()'
    result, port, model, providers, log = session(monkeypatch, tmp_path,
        ['前进10厘米然后分两次左转半圈', '再次执行', 'q'], [policy, policy])
    assert result == 0 and port.sent == ['t', 't', '@F100\n', 'j', 'j', 't',
                                       't', '@F100\n', 'j', 'j', 't', 't', 't']
    assert len(providers) == 1 and len(model.requests) == 2
    for task in log['tasks']:
        assert task['execution_success'] and task['last_feedback_state']['action_count'] == 4
        actions = [event for event in task['trace'] if event['phase'] == 'completed']
        assert [event['capability_id'] for event in actions] == ['motion.move', 'motion.turn', 'motion.turn', 'motion.stop']
        assert all(event['result']['timings_ms']['action_total'] >= 0 for event in actions)
    assert all(request[1]['max_motion_calls'] == 3 for request in model.requests)


def test_turn_back_receives_completed_sequence_even_after_model_refusal(monkeypatch, tmp_path):
    forward = 'robot.move(.1)\nrobot.turn(90)\nrobot.turn(90)\nrobot.stop()'
    back = 'robot.turn(-90)\nrobot.turn(-90)\nrobot.stop()'
    result, port, model, _, log = session(monkeypatch, tmp_path,
        ['前进10厘米左转90再左转90', '不支持的任务', '再转回去', 'q'],
        [forward, 'REFUSE: 不支持', back])
    assert result == 0 and port.sent == ['t', 't', '@F100\n', 'j', 'j', 't',
                                       't', 'l', 'l', 't', 't', 't']
    history = model.requests[2][1]['session_history']
    assert history['turn_back_reference']['inverse_turns_deg'] == [-90, -90]
    assert history['turn_back_reference']['task_number'] == 1
    assert history['recent_tasks'][1]['status'] == 'rejected_without_motion'
    assert log['tasks'][2]['model_history'] == history
    assert log['tasks'][2]['execution_success']
    assert model.requests[0][1]['session_history']['recent_tasks'] == []
    assert len(history['recent_tasks']) == 2  # Current task isn't its own history.


def test_new_console_does_not_reload_previous_motion_as_context(monkeypatch, tmp_path):
    session(monkeypatch, tmp_path, ['左转90度', 'q'], ['robot.turn(90)\nrobot.stop()'])
    result, port, model, _, log = session(monkeypatch, tmp_path, ['再转回去', 'q'],
        ['REFUSE: 本次会话没有已完成的转弯，请明确角度方向'])
    assert result == 0 and all(command == 't' for command in port.sent)
    history = model.requests[0][1]['session_history']
    assert history['recent_tasks'] == [] and history['turn_back_reference'] is None
    assert not log['tasks'][0]['execution_success']


def test_third_motion_waits_for_second_motion_stopped_ready():
    import threading
    from robot.backends.esp32_serial import build_esp32_registry
    from robot.runtime import RobotRuntime
    from robot.state import Pose
    from runtime.executor import PolicyExecutor
    backend, port = connected(responses={'j': transcript('j').removesuffix(READY)})
    limits = physical_control._physical_limits()
    runtime = RobotRuntime(backend, limits, build_esp32_registry(backend))
    released = threading.Event()
    def observe_then_release():
        with backend._condition:
            # Confirm the actual reader has processed DONE while READY is held.
            assert backend._condition.wait_for(lambda: backend._active and
                backend._active.key == 'j' and backend._active.done, timeout=2)
            assert port.sent.count('j') == 1 and not backend.snapshot()['ready']
            port.responses.pop('j')
            port.feed(READY)
            released.set()
    observer = threading.Thread(target=observe_then_release, daemon=True)
    observer.start()
    try:
        outcome = PolicyExecutor(limits).execute(
            'robot.move(.1)\nrobot.turn(90)\nrobot.turn(90)\nrobot.stop()',
            None, Pose(0, 0, 0), runtime=runtime)
        assert outcome.success and released.is_set() and port.sent.count('j') == 2
    finally:
        observer.join(2)
        backend.close()


@pytest.mark.parametrize('reply', [
    'VEHICLE_BLOCKED action=j cause=BMI_INVALID\n',
    'VEHICLE_START action=j ANGLE_TURN target_deg=90\nVEHICLE_STOP source=BOOT was_running=1;\n',
    'VEHICLE_START action=j ANGLE_TURN target_deg=90\nVEHICLE_STOP source=T was_running=1;\n',
])
def test_fault_or_external_stop_on_second_motion_cancels_third(monkeypatch, tmp_path, reply):
    port = FakePort(responses={'j': reply})
    result, port, model, _, log = session(monkeypatch, tmp_path, ['任务', '不应执行下一条', 'q'],
        ['robot.move(.1)\nrobot.turn(90)\nrobot.turn(90)\nrobot.stop()'], port=port)
    assert result == 1 and port.sent.count('@F100\n') == 1 and port.sent.count('j') == 1
    assert port.sent[-1] == 't' and not port.is_open
    assert len(model.requests) == 1 and not log['tasks'][0]['execution_success']


def test_four_motions_rejected_before_any_motion_in_session(monkeypatch, tmp_path):
    result, port, _, _, log = session(monkeypatch, tmp_path, ['任务', 'q'],
        ['robot.move(.1)\nrobot.turn(90)\nrobot.turn(90)\nrobot.move(.1)\nrobot.stop()'])
    assert result == 0 and all(command == 't' for command in port.sent)
    assert log['tasks'][0]['error'] and not log['tasks'][0]['policy_validated']


def test_second_motion_timeout_cancels_third_without_retry(monkeypatch, tmp_path):
    from dataclasses import replace
    build = physical_control.build_esp32_registry
    def short_turn_timeout(backend):
        registry = build(backend)
        item = registry.get('motion.turn')
        registry.unregister('motion.turn')
        registry.register(replace(item.spec, timeout_seconds=.05), item.handler, item.validator, backend.emergency_stop)
        return registry
    monkeypatch.setattr(physical_control, 'build_esp32_registry', short_turn_timeout)
    port = FakePort(responses={'j': 'VEHICLE_START action=j ANGLE_TURN target_deg=90\n'})
    result, port, model, _, log = session(monkeypatch, tmp_path, ['任务', '不应执行', 'q'],
        ['robot.move(.1)\nrobot.turn(90)\nrobot.turn(90)\nrobot.stop()'], port=port)
    assert result == 1 and port.sent.count('j') == 1 and port.sent[-1] == 't'
    assert len(model.requests) == 1 and not log['tasks'][0]['execution_success']


def test_idle_t_and_status_are_local_commands_without_model_calls(monkeypatch, tmp_path):
    result, port, model, providers, log = session(monkeypatch, tmp_path,
        ['status', 't', 'q'], [])
    assert result == 0 and not model.requests and port.sent == ['t', 't', 't', 't']


def test_faulted_action_ends_session_without_reading_or_running_next_task(monkeypatch, tmp_path):
    port = FakePort(responses={'f': F_START+F_STOP+F_RESULT.replace('encoder_valid=1', 'encoder_valid=0')+F_DONE+READY})
    result, port, model, providers, log = session(monkeypatch, tmp_path,
        ['前进30厘米停止', '右转90度停止', 'q'],
        ['robot.move(.3)\nrobot.stop()', 'robot.turn(-90)\nrobot.stop()'], port=port)
    assert result == 1 and len(model.requests) == 1 and 'l' not in port.sent
    assert len(log['tasks']) == 1 and log['error'] and not port.is_open


def test_stop_during_generation_sends_no_motion_and_ends_session(monkeypatch, tmp_path):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    def cancel(runtime, finished, cancelled):
        cancelled.set()
        runtime.emergency_stop()
    # session() installs a listener, so replace its setup wrapper for this test.
    def setup_then_cancel(runtime, finished, cancelled):
        cancel(runtime, finished, cancelled)
    from test_physical_policy import FakeModel
    monkeypatch.setattr(physical_control, '_keyboard_stop', setup_then_cancel)
    monkeypatch.setattr(physical_control, '_model_provider', lambda args: FakeModel(physical_control.DEMO_POLICY))
    monkeypatch.setattr(physical_control, '_read_instruction', lambda: '前进30厘米左转90度停止')
    log_path = tmp_path/'cancel.json'
    assert physical_control.main(['--interactive', '--execute', '--log', str(log_path)]) == 1
    assert all(k == 't' for k in port.sent) and not port.is_open
    log = json.loads(log_path.read_text(encoding='utf-8'))
    assert log['tasks'][0]['cancelled'] and not log['tasks'][0]['execution_success']


def test_cancelled_slow_model_does_not_delay_exit_or_later_move(monkeypatch, tmp_path):
    import threading
    import time
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    started, release = threading.Event(), threading.Event()
    class SlowModel:
        def generate_policy(self, *args):
            started.set()
            release.wait(2)
            return LLMResponse(physical_control.DEMO_POLICY, 'deepseek-flash', 1, 1, None, 1)
    def cancel(runtime, finished, cancelled):
        assert started.wait(1)
        cancelled.set()
        runtime.emergency_stop()
    monkeypatch.setattr(physical_control, '_keyboard_stop', cancel)
    monkeypatch.setattr(physical_control, '_model_provider', lambda args: SlowModel())
    monkeypatch.setattr(physical_control, '_read_instruction', lambda: '前进30厘米左转90度停止')
    try:
        began = time.monotonic()
        assert physical_control.main(['--interactive', '--execute', '--log', str(tmp_path/'slow.json')]) == 1
        assert time.monotonic()-began < 1
        assert all(k == 't' for k in port.sent) and not port.is_open
    finally:
        release.set()


def test_idle_boot_blocks_next_model_request_and_all_motion(monkeypatch, tmp_path):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    model = ScriptedModel([])
    monkeypatch.setattr(physical_control, '_model_provider', lambda args: model)
    def after_boot():
        port.feed('VEHICLE_STOP source=BOOT was_running=0; no queued motion.\n')
        # Queue observation is asynchronous. Wait for the reader's stop write.
        import time
        deadline = time.monotonic()+1
        while port.sent.count('t') < 2 and time.monotonic() < deadline:
            time.sleep(.005)
        return '右转90度停止'
    monkeypatch.setattr(physical_control, '_read_instruction', after_boot)
    assert physical_control.main(['--interactive', '--execute', '--log', str(tmp_path/'boot.json')]) == 1
    assert not model.requests and all(k == 't' for k in port.sent)


def test_input_eof_stops_and_exits_cleanly(monkeypatch, tmp_path):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    monkeypatch.setattr(physical_control, '_model_provider', lambda args: ScriptedModel([]))
    def eof():
        raise EOFError
    monkeypatch.setattr(physical_control, '_read_instruction', eof)
    assert physical_control.main(['--interactive', '--execute', '--log', str(tmp_path/'eof.json')]) == 0
    assert port.sent == ['t', 't', 't']


def test_ctrl_c_during_generation_records_cancellation_and_no_motion(monkeypatch, tmp_path):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    monkeypatch.setattr(physical_control, '_model_provider', lambda args: ScriptedModel([KeyboardInterrupt()]))
    monkeypatch.setattr(physical_control, '_read_instruction', lambda: '前进30厘米停止')
    path = tmp_path/'interrupt.json'
    assert physical_control.main(['--interactive', '--execute', '--log', str(path)]) == 1
    assert all(k == 't' for k in port.sent) and not port.is_open
    log = json.loads(path.read_text(encoding='utf-8'))
    assert log['tasks'][0]['cancelled'] and not log['tasks'][0]['execution_success']
    assert 'KeyboardInterrupt' in log['error']


def test_task_budget_renewal_requires_healthy_idle_and_is_not_policy_api():
    backend, port = connected()
    try:
        backend.move(.3)
        backend.turn(90)
        backend.stop()
        backend.begin_task()
        assert backend.snapshot()['action_count'] == 0
        backend.move(.3)
        assert backend.snapshot()['action_count'] == 1
        from robot.backends.esp32_serial import build_esp32_registry
        assert build_esp32_registry(backend).resolve_public_path(('begin_task',)) is None
        backend.emergency_stop()
        with pytest.raises(RobotEmergencyStopError):
            backend.begin_task()
    finally:
        backend.close()


def test_task_boundary_cannot_clear_pending_stop_or_active_action():
    backend, port = connected()
    try:
        backend._ready = False
        with pytest.raises(RobotEmergencyStopError):
            backend.begin_task()
        backend._ready = True
        backend._action_lock.acquire()
        from runtime.errors import RobotActionError
        with pytest.raises(RobotActionError):
            backend.begin_task()
        backend._action_lock.release()
    finally:
        backend.close()
