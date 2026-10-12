"""CLI lifecycle with fake serial; no COM port or keyboard access."""
import json
import pytest

import physical_control
from robot.backends.esp32_serial import ESP32SerialBackend
from test_esp32_serial import FakePort
from test_physical_policy import FakeModel


def install_fake_backend(monkeypatch, port):
    monkeypatch.setattr(physical_control, 'ESP32SerialBackend',
                        lambda *args, **kwargs: ESP32SerialBackend(*args, serial_factory=lambda: port, **kwargs))
    monkeypatch.setattr(physical_control, '_keyboard_stop', lambda runtime, finished, cancelled: finished.wait(5))


def test_status_cli_opens_and_stops_but_never_moves(monkeypatch, tmp_path):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    log = tmp_path/'status.json'
    assert physical_control.main(['--log', str(log)]) == 0
    assert port.sent == ['t', 't'] and not port.is_open
    record = json.loads(log.read_text(encoding='utf-8'))
    assert record['status_only'] and record['task_success'] is None
    assert record['last_feedback_state']['stopped'] is True
    assert record['last_feedback_state']['pose_available'] is False


def test_demo_cli_runs_policy_worker_and_saves_real_feedback(monkeypatch, tmp_path):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    log = tmp_path/'demo.json'
    assert physical_control.main(['--demo', '--log', str(log)]) == 0
    assert port.sent == ['t', 'f', 'j', 't', 't'] and not port.is_open
    record = json.loads(log.read_text(encoding='utf-8'))
    assert record['execution_success'] and record['task_success'] is None
    assert record['worker_outcome']['final_state']['stopped'] is True
    assert [e['capability_id'] for e in record['trace'] if e['phase'] == 'completed'] == ['motion.move', 'motion.turn', 'motion.stop']
    assert 'pose' not in record['last_feedback_state']


def test_cli_cancellation_prevents_entire_remaining_policy(monkeypatch, tmp_path):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    def cancel(runtime, finished, cancelled):
        cancelled.set()
        runtime.emergency_stop()
    monkeypatch.setattr(physical_control, '_keyboard_stop', cancel)
    log = tmp_path/'cancelled.json'
    assert physical_control.main(['--demo', '--log', str(log)]) == 1
    assert all(key == 't' for key in port.sent)
    record = json.loads(log.read_text(encoding='utf-8'))
    assert record['cancelled'] and not record['execution_success']


def test_natural_preview_never_opens_serial(monkeypatch, tmp_path):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    monkeypatch.setattr(physical_control, '_model_provider', lambda args: FakeModel(physical_control.DEMO_POLICY))
    log = tmp_path/'preview.json'
    assert physical_control.main(['--instruction', '前进30厘米，左转90度，停止', '--log', str(log)]) == 0
    assert port.sent == [] and not port.reset_called and not port.is_open
    record = json.loads(log.read_text(encoding='utf-8'))
    assert record['preview_only'] and record['policy_validated'] and not record['execution_success']
    assert record['generation']['model'] == 'deepseek-flash'
    assert record['serial_log'] == [] and not record['status_only']
    assert set(record['timings_ms']) == {'policy_lookup','model_request', 'policy_validation', 'task_total'}
    assert all(value >= 0 for value in record['timings_ms'].values())


def test_natural_execute_uses_model_plan_and_real_worker(monkeypatch, tmp_path):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    monkeypatch.setattr(physical_control, '_model_provider', lambda args: FakeModel('robot.turn(-90)\nrobot.stop()'))
    log = tmp_path/'natural.json'
    assert physical_control.main(['--instruction', '右转90度，停止', '--execute', '--log', str(log)]) == 0
    assert port.sent == ['t', 'l', 't', 't'] and not port.is_open
    record = json.loads(log.read_text(encoding='utf-8'))
    assert record['execution_success'] and record['generation']['input_tokens'] == 100
    assert record['task_success'] is None and record['policy_validated']


@pytest.mark.parametrize('policy', [
    'robot.move(0.3)\nrobot.turn(5)\nrobot.stop()',
    'for i in range(5):\n    robot.move(0.3)\nrobot.stop()',
    'REFUSE: 不支持此动作。',
    'robot.move(.1)\nrobot.turn(90)\nrobot.turn(5)\nrobot.stop()',
    'robot.move(.1)\nrobot.turn(90)\nrobot.turn(90)\nrobot.move(.1)\nrobot.stop()',
])
def test_invalid_natural_plan_never_opens_serial_even_with_execute(monkeypatch, tmp_path, policy):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    monkeypatch.setattr(physical_control, '_model_provider', lambda args: FakeModel(policy))
    log = tmp_path/'rejected.json'
    assert physical_control.main(['--instruction', '测试', '--execute', '--log', str(log)]) == 1
    assert port.sent == [] and not port.reset_called
    record = json.loads(log.read_text(encoding='utf-8'))
    assert record['error'] and not record['policy_validated'] and not record['execution_success']


def test_generation_error_has_no_serial_side_effects(monkeypatch, tmp_path):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    def fail(args):
        raise ValueError('No model configuration')
    monkeypatch.setattr(physical_control, '_model_provider', fail)
    assert physical_control.main(['--instruction', '停止', '--execute', '--log', str(tmp_path/'error.json')]) == 1
    assert port.sent == []


def test_execute_flag_requires_instruction():
    with pytest.raises(SystemExit) as exc:
        physical_control.main(['--execute'])
    assert exc.value.code == 2


def test_key_is_prompted_locally_and_not_written_to_preview_log(monkeypatch, tmp_path):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr('getpass.getpass', lambda prompt: 'unit-test-secret')
    def provider(**kwargs):
        assert kwargs['api_key'] == 'unit-test-secret'
        assert kwargs['model'] == 'deepseek-flash'
        return FakeModel('robot.stop()')
    monkeypatch.setattr(physical_control, 'OpenAICompatibleProvider', provider)
    log = tmp_path/'no-secret.json'
    assert physical_control.main(['--instruction', '停止', '--log', str(log)]) == 0
    assert 'unit-test-secret' not in log.read_text(encoding='utf-8')
    assert port.sent == []


def test_failed_log_write_returns_failure_in_preview(monkeypatch, tmp_path):
    port = FakePort()
    install_fake_backend(monkeypatch, port)
    monkeypatch.setattr(physical_control, '_model_provider', lambda args: FakeModel('robot.stop()'))
    assert physical_control.main(['--instruction', '停止', '--log', str(tmp_path)]) == 1
    assert port.sent == []
