"""Configuration compatibility, per-action prerequisites and honest ACK timing."""
from copy import deepcopy
import pytest

from agent.physical_policy import capture_physical_feedback, generate_physical_policy
from policy.episode import EpisodeRecord
from robot.backends.esp32_serial import build_esp32_registry
from robot.physical_status import action_availability, diagnose
from runtime.errors import RobotActionError, UnsafePolicyError
from test_manual_pwm import pwm_hardware
from test_policy_reuse import train, INSTRUCTION, CountingModel
from test_webui import CLIENT, connect, wait_job
from webui.controller import ControlDesk


IDLE = {'connected':True, 'ready':True, 'stopped':True, 'firmware':'1.9',
        'pwm_manual_available':True, 'fault':None}


def test_heading_failure_does_not_disable_pwm_and_range_failure_does_not_disable_turn():
    facts=action_availability(IDLE, {'get_attitude':{'valid':False},'get_distance':{'valid':True}})
    assert facts['manual_pwm']['available'] and not facts['move']['available']
    assert not facts['approach']['available'] and facts['manual_reverse_timed']['available']
    facts=action_availability(IDLE, {'get_attitude':{'valid':True},'get_distance':{'valid':False}})
    assert facts['turn']['available'] and facts['move']['available']
    assert not facts['approach']['available']


def test_fault_blocks_every_motion_but_never_stop():
    state={**IDLE,'fault':'VEHICLE_BLOCKED cause=HEADING_BMI_INVALID_OR_STALE'}
    facts=action_availability(state, {'get_attitude':{'valid':True}})
    assert facts['stop']['available'] and not facts['manual_pwm']['available']
    assert diagnose(state)['code']=='heading_unavailable'
    assert '归零' in diagnose(state)['recovery']


def test_bool_only_sensor_validity_and_unsupported_old_firmware():
    facts=action_availability({**IDLE,'firmware':'1.8'}, {'get_attitude':{'valid':'1'}})
    assert not facts['move']['available'] and not facts['manual_pwm']['available']
    assert not facts['reverse']['available']


def test_calibration_profile_change_misses_cache_without_destroying_previous_record(tmp_path):
    desk,_=train(tmp_path)
    try:
        registry=build_esp32_registry(desk.backend)
        assert desk.policy_library.lookup(INSTRUCTION,registry)[0]
        desk.backend.configure_range_offset(-20)
        assert desk.policy_library.lookup(INSTRUCTION,registry)[0] is None
        assert desk.policy_library.snapshot()['verified_recipes']==1
        desk.backend.configure_range_offset(0)
        assert desk.policy_library.lookup(INSTRUCTION,registry)[0]
        episode=deepcopy(desk.tasks[-1]['episode'])
        episode['initial_state']['execution_profile']['range_offset_mm']=42
        assert desk.policy_library.record(EpisodeRecord(**episode),registry) is None
    finally: desk.close()


def test_firmware_build_change_misses_cache_but_volatile_boot_does_not(tmp_path):
    desk,_=train(tmp_path)
    try:
        registry=build_esp32_registry(desk.backend)
        original=registry.execution_profile()
        registry.set_execution_profile(lambda: {**original,'firmware_build':'new-build'})
        assert desk.policy_library.lookup(INSTRUCTION,registry)[0] is None
        assert 'boot_id' not in original and 'yaw' not in original
    finally: desk.close()


def test_stale_preview_cannot_execute_after_unannounced_calibration_change(tmp_path):
    desk=ControlDesk(demo=True,provider_factory=lambda:CountingModel(),log_dir=tmp_path,monitor=False)
    try:
        connect(desk);desk.generate(CLIENT,INSTRUCTION)
        assert wait_job(desk)['status']=='completed'
        plan=desk.snapshot()['plan']
        desk.backend.configure_range_offset(-20)
        with pytest.raises(ValueError,match='配置已变化'): desk.execute(CLIENT,plan['id'])
        assert desk.backend.action_count==0
    finally:desk.close()


def test_ui_and_model_receive_same_preflight(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        connect(desk)
        feedback=capture_physical_feedback(desk.backend)
        assert desk.snapshot()['action_availability']==feedback['action_availability']
        assert feedback['diagnosis']['code']=='healthy_idle'
    finally:desk.close()


def test_model_cannot_override_invalid_range_and_turn_does_not_need_it(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        connect(desk); registry=build_esp32_registry(desk.backend)
        feedback=capture_physical_feedback(desk.backend)
        feedback['sensors']['get_distance']['valid']=False
        # Even a stale advisory table claiming "available" cannot override fresh typed facts.
        model=CountingModel('robot.approach(.1)\nrobot.stop()')
        with pytest.raises(UnsafePolicyError,match='range_unavailable'):
            generate_physical_policy(model,'走到前方10厘米',registry,feedback=feedback)
        model=CountingModel('robot.turn(30)\nrobot.stop()')
        assert generate_physical_policy(model,'左转30度',registry,feedback=feedback)[0].startswith('robot.turn')
    finally:desk.close()


def test_verified_cache_cannot_override_invalid_heading(tmp_path):
    desk,_=train(tmp_path)
    try:
        feedback=capture_physical_feedback(desk.backend)
        feedback['sensors']['get_attitude']['valid']=False
        def no_model():raise AssertionError('cache hit must not request the model')
        with pytest.raises(UnsafePolicyError,match='heading_unavailable'):
            generate_physical_policy(no_model,INSTRUCTION,build_esp32_registry(desk.backend),
                                     feedback=feedback,policy_library=desk.policy_library)
        assert desk.backend.stopped
    finally:desk.close()


def test_firmware_ack_timing_only_accepts_current_session_and_stays_first_ack():
    backend,port=pwm_hardware()
    try:
        backend.teleop_start('forward',pwm_percent=40)
        with backend._condition:
            assert backend._condition.wait_for(lambda: backend.snapshot()['teleop_timing']['first_firmware_ack_ms'] is not None, timeout=.5)
        first=backend.snapshot()['teleop_timing']['first_firmware_ack_ms']
        assert first>=0
        backend._on_line('TELEOP_ACK session=999 applied_a=100 applied_b=100')
        backend.teleop_pulse(pwm_percent=100)
        assert backend.snapshot()['teleop_timing']['first_firmware_ack_ms']==first
        backend.teleop_release()
        assert port.sent[-1]=='t'
    finally: backend.close()


def test_disconnected_backend_cannot_expose_fresh_attitude():
    backend,port=pwm_hardware()
    try:
        backend._on_line('ATTITUDE roll=1 pitch=2 yaw=3 tilt_valid=1 yaw_valid=1')
        assert backend.get_attitude()['yaw']==3
        backend._established=False
        with pytest.raises(RobotActionError):backend.get_attitude()
    finally:backend.close()
