"""Model output preflight; no network, serial port or physical motion."""
import json

import pytest

from agent.physical_policy import generate_physical_policy, validate_physical_policy, capture_physical_feedback
from agent.physical_context import build_physical_history
from providers.base import LLMResponse
from robot.backends.esp32_serial import ESP32SerialBackend, build_esp32_registry
from runtime.errors import PolicyError, UnsafePolicyError


class FakeModel:
    def __init__(self, text):
        self.text = text
        self.requests = []

    def generate_policy(self, task, robot_api, world_state, system_prompt=None):
        self.requests.append((task, robot_api, world_state, system_prompt))
        return LLMResponse(self.text, 'deepseek-flash', 100, 20, None, 12)


def hardware_registry():
    return build_esp32_registry(ESP32SerialBackend())


@pytest.mark.parametrize('policy', [
    'robot.stop()',
    'robot.move(0.30)\nrobot.stop()',
    'robot.turn(-90)\nrobot.stop()',
    'robot.move(distance=0.3)\nrobot.turn(angle=+90)\nrobot.stop()',
    'robot.move(0.2)\nrobot.turn(45)\nrobot.stop()',
    'robot.move(0.05)\nrobot.turn(-15.1)\nrobot.stop()',
    'robot.move(0.1)\nrobot.turn(90)\nrobot.turn(90)\nrobot.stop()',
    'robot.turn(-45)\nrobot.turn(-45)\nrobot.turn(-45)\nrobot.stop()',
])
def test_supported_literal_plan(policy):
    validate_physical_policy(policy, hardware_registry())


@pytest.mark.parametrize('policy', [
    '', 'robot.move(0.3)',
    'robot.move(0.3)\nrobot.turn(5)\nrobot.stop()',  # reject before ANY move
    'robot.move(0.02)\nrobot.stop()', 'robot.move(-0.3)\nrobot.stop()',
    'robot.move(0.2005)\nrobot.stop()', 'robot.turn(45.05)\nrobot.stop()',
    'robot.move(30)\nrobot.stop()', 'robot.move(True)\nrobot.stop()',
    'robot.turn(1e309)\nrobot.stop()', 'robot.turn(90*1)\nrobot.stop()',
    'robot.turn(--90)\nrobot.stop()',
    'robot.move(0.3, speed=0.1)\nrobot.stop()',
    'robot.get_distance(1)\nrobot.stop()', 'robot.get_pose()\nrobot.stop()',
    'robot.get_state(x=1)\nrobot.stop()',
    'robot.move(0.3)\nrobot.turn(90)\nrobot.turn(-90)\nrobot.move(.1)\nrobot.stop()',
    'robot.move(0.1)\nrobot.turn(90)\nrobot.turn(5)\nrobot.stop()',
    'robot.move(0.1)\nrobot.turn(90)\nrobot.turn(90)',
    'for i in range(2):\n    robot.move(0.3)\nrobot.stop()',
    'if True:\n    robot.move(0.3)\nrobot.stop()',
    'd = 0.3\nrobot.move(d)\nrobot.stop()',
    'robot.stop()\nrobot.move(0.3)\nrobot.stop()',
    'import os\nrobot.stop()',
    'robot.stop()\n'+('#'+('x'*4100)),
])
def test_entire_plan_rejected(policy):
    with pytest.raises(PolicyError):
        validate_physical_policy(policy, hardware_registry())


def test_prompt_uses_hardware_contract_without_dummy_world():
    model = FakeModel('```python\nrobot.move(0.3)\nrobot.turn(-90)\nrobot.stop()\n```')
    policy, response = generate_physical_policy(model, '前进30厘米，右转90度，停止', hardware_registry())
    assert 'robot.turn(-90)' in policy and response.model == 'deepseek-flash'
    task, api, context, system = model.requests[0]
    assert 'get_pose' not in api and 'get_distance' in api
    data = json.loads(context)
    assert data['front_guard'] == 'OFF' and not data['pose_available']
    assert 'pose' not in data and 'world' not in data and not data['serial_connected']
    assert 'not round' in system and 'At most 3' in system
    assert 'finite repetitions are allowed' in system and data['max_motion_calls'] == 3
    assert task == '前进30厘米，右转90度，停止'


def test_model_receives_explicit_context_and_context_does_not_bypass_bounds():
    history = build_physical_history([])
    model = FakeModel('robot.turn(180)\nrobot.stop()')
    with pytest.raises(PolicyError):
        generate_physical_policy(model, '再转回去', hardware_registry(), history=history)
    _, _, context, system = model.requests[0]
    assert json.loads(context)['session_history'] == history
    assert 'inverse_turns_deg' in system and 'not gyro overshoot' in system
    assert 'never infer reverse' in system.lower()


@pytest.mark.parametrize('text', ['REFUSE: 目前不支持后退。', 'robot.move(0.5)\nrobot.stop()', ''])
def test_model_refusal_or_bad_response_is_not_executable(text):
    with pytest.raises(PolicyError):
        generate_physical_policy(FakeModel(text), '前进50厘米', hardware_registry())


@pytest.mark.parametrize('instruction', ['', '   ', 'x'*2001])
def test_invalid_instruction_does_not_call_model(instruction):
    model = FakeModel('robot.stop()')
    with pytest.raises(UnsafePolicyError):
        generate_physical_policy(model, instruction, hardware_registry())
    assert not model.requests


def test_current_valid_sensors_are_explicit_even_after_failed_history():
    from webui.demo import DemoBackend
    backend=DemoBackend()
    backend.connect()
    backend.range_mm=265
    feedback=capture_physical_feedback(backend)
    assert feedback['sensors']['get_attitude']['valid'] is True
    assert feedback['sensors']['get_distance']['valid'] is True
    model=FakeModel('robot.approach(.1)\nrobot.stop()')
    history=build_physical_history([{'number':1,'instruction':'旧失败任务','error':'BMI invalid','trace':[]}])
    generate_physical_policy(model,'走到距离前方10厘米',build_esp32_registry(backend),feedback=feedback,history=history)
    context=json.loads(model.requests[0][2])
    assert context['sensors']['get_distance']['distance_m']==.265
    assert context['sensors']['get_attitude']['valid'] is True
    assert context['session_history']['recent_tasks'][0]['status']=='rejected_without_motion'


def test_failed_sensor_is_never_relabelled_as_valid():
    from runtime.errors import RobotActionError
    class Backend:
        def snapshot(self):return {'connected':True,'firmware':'1.9'}
        def get_distance(self):return {'valid':True,'distance_m':.265}
        def get_attitude(self):raise RobotActionError('Invalid yaw')
    feedback=capture_physical_feedback(Backend())
    assert feedback['sensors']['get_distance']['valid'] is True
    assert feedback['sensors']['get_attitude']=={
        'valid':False,'reason':'unavailable_or_stale','error_type':'RobotActionError'}
