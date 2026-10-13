"""Recovery transcripts use fake serial; never probe or stall a real motor."""
import time
import pytest
from robot.backends.esp32_serial import ESP32SerialBackend
from robot.physical_status import diagnose
from runtime.errors import RobotActionTimeoutError, RobotFirmwareFaultError
from test_esp32_serial import FakePort, ACK
from test_manual_pwm import PWM_READY
from test_webui import CLIENT, wait_job
from webui.controller import ControlDesk


def fake_pwm_backend():
    replies={f'@U{session*10000+1000+percent}\n':
             f'TELEOP_ACK session={session} direct_pwm=1 pwm_percent={percent} applied_a={percent} applied_b={percent}\n'
             for session in (1,2) for percent in (20,40)}
    port=FakePort(handshake=ACK+PWM_READY,responses=replies)
    backend=ESP32SerialBackend(serial_factory=lambda:port)
    return backend,port


def test_low_pwm_end_cuts_session_without_fault_or_automatic_motion():
    backend,port=fake_pwm_backend();backend.connect(timeout=.5)
    try:
        backend.teleop_start('forward',pwm_percent=20)
        backend._on_line('TELEOP_END cause=LOW_PWM_NO_ENCODER_EDGES session=1 motor=A pwm_percent=20 fault=0')
        before=list(port.sent)
        result=backend.teleop_pulse(pwm_percent=40)
        assert result['holding'] is False and result['control_stop']['code']=='LOW_PWM_NO_ENCODER_EDGES'
        assert port.sent==before and not backend.snapshot()['fault']
        assert not backend.snapshot()['ready']
        backend._on_line(PWM_READY.strip()+' teleop_next=2')
        assert backend.snapshot()['ready'] and backend.snapshot()['stopped']
        assert backend.teleop_pulse(pwm_percent=40)['holding'] is False
        assert port.sent==before  # A renewed pulse cannot resume even after READY.
        backend.teleop_start('forward',pwm_percent=40)
        assert port.sent[-1]=='@U21040\n' and backend.snapshot()['control_stop'] is None
    finally:backend.close()


def test_delayed_end_from_previous_press_cannot_stop_current_press():
    backend,port=fake_pwm_backend();backend.connect(timeout=.5)
    try:
        backend.teleop_start('forward',pwm_percent=40)
        backend._on_line('TELEOP_END cause=LOW_PWM_NO_ENCODER_EDGES session=999 fault=0')
        assert backend.snapshot()['control_stop'] is None
        backend.teleop_pulse(pwm_percent=40)
        assert port.sent[-1]=='@U11040\n'
    finally:backend.close()


def test_manual_encoder_variation_requires_fresh_press_even_after_ready():
    backend,port=fake_pwm_backend();backend.connect(timeout=.5)
    try:
        backend.teleop_start('forward',pwm_percent=40)
        backend._on_line('TELEOP_END cause=MANUAL_ENCODER_VARIATION session=1 motor=A pwm_percent=40 fault=0 dq=36 edges=48 difference=12')
        before=list(port.sent)
        result=backend.teleop_pulse(pwm_percent=40)
        assert result['holding'] is False and result['control_stop']['code']=='MANUAL_ENCODER_VARIATION'
        assert backend.snapshot()['fault'] is None
        backend._on_line(PWM_READY.strip()+' teleop_next=2')
        assert backend.teleop_pulse(pwm_percent=40)['holding'] is False
        assert port.sent==before
        backend.teleop_start('forward',pwm_percent=40)
        assert port.sent[-1]=='@U21040\n'
    finally:backend.close()


def test_latched_stop_handshake_fails_fast_with_reset_guidance():
    port=FakePort(handshake='VEHICLE_STOP source=T was_running=0 fault=1; no queued motion.\n')
    backend=ESP32SerialBackend(serial_factory=lambda:port)
    started=time.monotonic()
    with pytest.raises(RobotFirmwareFaultError,match='EN/RST'):backend.connect(timeout=2)
    assert time.monotonic()-started<1
    assert set(port.sent)=={'t'} and not port.is_open
    assert backend.snapshot()['firmware_fault_latched']
    assert diagnose(backend.snapshot())['code']=='firmware_latched'


@pytest.mark.parametrize('reply,expected',[(ACK,'未返回停稳 READY'),('', '没有收到 STOP 确认')])
def test_old_firmware_missing_ready_is_not_reported_as_generic_handshake(reply,expected):
    port=FakePort(handshake=reply);backend=ESP32SerialBackend(serial_factory=lambda:port)
    with pytest.raises(RobotActionTimeoutError,match=expected):backend.connect(timeout=.05)
    assert set(port.sent)=={'t'} and not port.is_open


def test_controller_releases_soft_stop_without_reconnect_and_drops_faulted_hold_on_reconnect(tmp_path):
    old,port=fake_pwm_backend()
    fresh,fresh_port=fake_pwm_backend()
    desk=ControlDesk(backend_factory=lambda name:fresh,log_dir=tmp_path,monitor=False)
    try:
        old.connect(timeout=.5);desk.backend=old
        desk.teleop_start(CLIENT,'forward',pwm_percent=20)
        old._on_line('TELEOP_END cause=LOW_PWM_NO_ENCODER_EDGES session=1 fault=0')
        assert desk.teleop_pulse(CLIENT,pwm_percent=20)['holding'] is False
        assert desk.holding is None and not old.snapshot()['fault']
        old._on_line(PWM_READY.strip()+' teleop_next=2')
        desk.teleop_start(CLIENT,'forward',pwm_percent=40)
        old._on_line('E (1) dual-test: STOP motor=A reason=NO_NEW_ENCODER_EDGES')
        assert old.snapshot()['firmware_fault_latched']
        desk.connect(CLIENT,'COM5')
        assert wait_job(desk)['status']=='completed'
        assert desk.holding is None and fresh_port.sent==['t']
    finally:desk.close()
