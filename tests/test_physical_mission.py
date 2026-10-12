"""Real Worker with fake serial/model only: mission progression and stop bounds."""
import json
import threading
import time

import pytest

import main
import physical_control
from agent.physical_mission import MissionGuard, split_mission
from providers.base import LLMResponse
from runtime.errors import PolicyError
from test_esp32_serial import FakePort, READY, F_START, F_STOP, F_RESULT, F_DONE
from test_physical_control import install_fake_backend
from test_physical_policy import hardware_registry
from test_physical_session import ScriptedModel


FOUR = 'robot.move(.1)\nrobot.turn(30)\nrobot.turn(-30)\nrobot.move(.2)\nrobot.stop()'
NINE = '\n'.join(['robot.move(.1)', 'robot.turn(30)', 'robot.turn(-30)']*3+['robot.stop()'])


def run_session(monkeypatch, tmp_path, responses, *, execute=True, port=None, model=None, lines=None):
    port = port or FakePort()
    install_fake_backend(monkeypatch, port)
    model = model or ScriptedModel(responses)
    monkeypatch.setattr(physical_control, '_model_provider', lambda args: model)
    inputs = iter(lines or ['前进10厘米左转30右转30再前进20厘米停止', 'q'])
    monkeypatch.setattr(physical_control, '_read_instruction', lambda: next(inputs))
    path = tmp_path/'mission.json'
    args = ['--backend', 'esp32', '--interactive', '--mission', '--log', str(path)]
    code = main.main(args+(['--execute'] if execute else []))
    return code, port, model, json.loads(path.read_text(encoding='utf-8'))


def test_nine_motion_plan_is_split_but_tenth_or_late_bad_call_is_rejected():
    registry = hardware_registry()
    parts = split_mission(NINE, registry)
    assert len(parts) == 3 and all(len(p.splitlines()) == 4 for p in parts)
    assert split_mission('robot.stop()', registry) == ['robot.stop()']
    for policy in (NINE.replace('robot.stop()', 'robot.move(.1)\nrobot.stop()'),
                   FOUR.replace('robot.move(.2)', 'robot.turn(180)')):
        with pytest.raises(PolicyError):
            split_mission(policy, registry)


def test_unified_default_stays_virtual_and_esp32_dispatch_does_not_load_world(monkeypatch):
    calls = []
    def no_world(*args):
        raise AssertionError('Hardware dispatch must not load virtual config')
    monkeypatch.setattr(main, 'load_config', no_world)
    monkeypatch.setattr(physical_control, 'main', lambda argv: calls.append(argv) or 7)
    assert main.main(['--backend', 'esp32', '--interactive', '--mission', '--execute']) == 7
    assert calls == [['--interactive', '--mission', '--execute']]
    with pytest.raises(AssertionError, match='Hardware dispatch'):
        main.main(['--auto', '前进10厘米'])


def test_mission_flag_requires_interactive_and_simulation_flags_never_trigger_hardware():
    with pytest.raises(SystemExit) as exc:
        physical_control.main(['--mission', '--instruction', '停止'])
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        main.main(['--backend', 'esp32', '--auto'])


def test_preview_validates_whole_plan_without_serial_or_invented_feedback(monkeypatch, tmp_path):
    code, port, model, log = run_session(monkeypatch, tmp_path, [NINE], execute=False)
    assert code == 0 and not port.sent and not port.reset_called
    task = log['tasks'][0]
    assert task['policy_validated'] and not task['execution_success']
    assert len(task['segment_policies']) == 3 and not task['segments'] and not task['reviews']
    assert len(model.requests) == 1 and model.requests[0][1]['max_motion_calls'] == 9
    assert task['completed_motion_calls'] == 0 and task['task_success'] is None


@pytest.mark.parametrize('policy', [
    FOUR.replace('robot.move(.2)', 'robot.turn(180)'),
    NINE.replace('robot.stop()', 'robot.move(.1)\nrobot.stop()'),
    'REFUSE: 不支持后退',
])
def test_entire_bad_plan_never_moves_including_its_valid_first_segment(monkeypatch, tmp_path, policy):
    code, port, _, log = run_session(monkeypatch, tmp_path, [policy])
    assert code == 0 and all(wire == 't' for wire in port.sent)
    assert log['tasks'][0]['error'] and not log['tasks'][0]['policy_validated']


def test_three_segments_feed_actual_results_and_no_retry_then_new_task_budget(monkeypatch, tmp_path):
    code, port, model, log = run_session(monkeypatch, tmp_path,
        [NINE, 'CONTINUE', 'CONTINUE', 'robot.turn(-45)\nrobot.stop()'], lines=['三段动作', '右转45度', 'q'])
    assert code == 0
    task = log['tasks'][0]
    assert task['execution_success'] and task['completed_motion_calls'] == 9
    assert len(task['segments']) == 3 and len(task['reviews']) == 2
    assert all(segment['execution_success'] for segment in task['segments'])
    movements = [w for w in port.sent if w != 't']
    assert movements == ['@F100\n', '@J300\n', '@L300\n']*3+['@L450\n']
    for i, (_, state) in enumerate(model.requests[1:3]):
        assert state['feedback'] == {'connected': True, 'ready': True, 'stopped': True, 'fault': None}
        actions = state['completed_segment']['completed_actions']
        assert [a['name'] for a in actions] == ['move', 'turn', 'turn', 'stop']
        assert actions[0]['after_stop_mean_mm'] >= 100 and 'after_stop_deg' in actions[1]
        assert state['completed_segment']['status'] == 'completed'
        assert len(state['remaining_segments']) == 2-i
        assert state['pose_available'] is False and state['range_available'] is False
    assert log['tasks'][1]['execution_success']
    assert model.requests[-1][1]['session_history']['recent_tasks'][0]['status'] == 'completed'
    assert all(task['task_success'] is None for task in log['tasks'])


@pytest.mark.parametrize('reply', ['ABORT: 反馈不确定', 'robot.move(.3)\nrobot.stop()',
                                  'CONTINUE\nrobot.turn(90)', 'DONE', 'continue'])
def test_review_abort_or_extra_code_never_executes_remaining_plan(monkeypatch, tmp_path, reply):
    code, port, model, log = run_session(monkeypatch, tmp_path, [FOUR, reply])
    assert code == 1 and len(model.requests) == 2
    assert [w for w in port.sent if w != 't'] == ['@F100\n', '@J300\n', '@L300\n']
    task = log['tasks'][0]
    assert not task['execution_success'] and task['error'] and task['completed_motion_calls'] == 3
    assert len(task['segments']) == 1 and not port.is_open


def test_invalid_hardware_feedback_stops_before_model_review(monkeypatch, tmp_path):
    port = FakePort(responses={'f': F_START+F_STOP+F_RESULT.replace('encoder_valid=1', 'encoder_valid=0')+F_DONE+READY})
    policy = FOUR.replace('robot.move(.1)', 'robot.move(.3)')
    code, port, model, log = run_session(monkeypatch, tmp_path, [policy], port=port)
    assert code == 1 and len(model.requests) == 1 and not log['tasks'][0]['reviews']
    assert [w for w in port.sent if w != 't'] == ['f']


def test_task_deadline_aborts_slow_initial_model_without_motion(monkeypatch, tmp_path):
    release = threading.Event()
    class SlowModel:
        def generate_policy(self, *args):
            release.wait(3)
            return LLMResponse(FOUR, 'fake', 1, 1, None, 1)
    monkeypatch.setattr(physical_control, 'MissionGuard',
                        lambda *args: MissionGuard(*args, timeout=.06))
    try:
        started = time.monotonic()
        code, port, _, log = run_session(monkeypatch, tmp_path, [], model=SlowModel())
        assert code == 1 and time.monotonic()-started < 1
        assert all(w == 't' for w in port.sent)
        assert log['tasks'][0]['mission_deadline_expired'] and log['tasks'][0]['cancelled']
    finally:
        release.set()


def test_task_deadline_stops_during_motion_and_never_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(physical_control, 'MissionGuard',
                        lambda *args: MissionGuard(*args, timeout=.5))
    port = FakePort(responses={'f': F_START})
    policy = FOUR.replace('robot.move(.1)', 'robot.move(.3)')
    code, port, model, log = run_session(monkeypatch, tmp_path, [policy], port=port)
    assert code == 1 and [w for w in port.sent if w != 't'] == ['f']
    assert len(model.requests) == 1 and log['tasks'][0]['mission_deadline_expired']
    assert not log['tasks'][0]['execution_success']


def test_cancelled_review_late_continue_cannot_start_next_segment(monkeypatch, tmp_path):
    review_started, release = threading.Event(), threading.Event()
    class SlowReviewModel(ScriptedModel):
        def generate_policy(self, task, api, state, system=None):
            if 'completed_segment' in json.loads(state):
                review_started.set()
                release.wait(3)
            return super().generate_policy(task, api, state, system)
    model = SlowReviewModel([FOUR, 'CONTINUE'])
    # Install listener after run_session's fake setup, but retain real backend.
    install = install_fake_backend
    def setup(monkeypatch, port):
        install(monkeypatch, port)
        def cancel(runtime, finished, cancelled):
            if review_started.wait(2):
                cancelled.set()
                runtime.emergency_stop()
        monkeypatch.setattr(physical_control, '_keyboard_stop', cancel)
    monkeypatch.setattr(__import__(__name__), 'install_fake_backend', setup)
    try:
        code, port, _, log = run_session(monkeypatch, tmp_path, [], model=model)
        assert code == 1 and log['tasks'][0]['cancelled']
        wires = list(port.sent)
        release.set()
        time.sleep(.05)
        assert port.sent == wires and '@F200\n' not in port.sent
    finally:
        release.set()
