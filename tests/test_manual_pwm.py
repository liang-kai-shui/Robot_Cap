"""PWM routing and feedback tests use fake serial only; never drive hardware."""
import pytest
from robot.backends.esp32_serial import ESP32SerialBackend
from runtime.errors import RobotActionError
from test_esp32_serial import FakePort, ACK
from test_rover_optimization import READY2, hardware
from webui.controller import ControlDesk
from test_webui import CLIENT, wait_job

PWM_READY=READY2.replace('protocol=PARAM2','protocol=PARAM2 build=pwm-stream-v1 teleop_pwm=1')

def pwm_hardware():
    replies={f'@U{11000+n}\n':f'TELEOP_ACK session=1 direction=1 direct_pwm=1 pwm_percent={n} applied_a={n} applied_b={n}\n' for n in (1,40,100)}
    port=FakePort(handshake=ACK+PWM_READY,responses=replies)
    backend=ESP32SerialBackend(serial_factory=lambda:port)
    backend.connect(timeout=.5)
    return backend,port

def test_live_pwm_changes_keep_press_and_stop_priority():
    backend,port=pwm_hardware()
    try:
        assert backend.snapshot()['pwm_manual_available']
        assert backend.snapshot()['firmware_build']=='pwm-stream-v1'
        backend.teleop_start('forward',pwm_percent=40)
        with backend._condition:
            assert backend._condition.wait_for(lambda:backend.snapshot()['manual_pwm_actual']=={'A':40,'B':40},timeout=.5)
        backend.teleop_pulse(pwm_percent=100)
        backend.teleop_pulse(pwm_percent=1)
        assert port.sent==['t','@U11040\n','@U11100\n','@U11001\n']
        backend.teleop_release()
        backend._on_line('TELEOP_ACK session=1 direct_pwm=1 applied_a=100 applied_b=100')
        assert backend.snapshot()['stopped'] is not False
        with backend._condition: assert backend._condition.wait_for(lambda:backend._ready,timeout=.5)
        assert port.sent[-1]=='t' and not backend.snapshot()['teleop']
    finally:backend.close()

@pytest.mark.parametrize('value',[0,101,-1,True,40.0,'40'])
def test_bad_pwm_never_writes_motion(value):
    backend,port=pwm_hardware()
    try:
        with pytest.raises(RobotActionError):backend.teleop_start('forward',pwm_percent=value)
        assert port.sent==['t']
    finally:backend.close()

def test_old_firmware_cannot_pretend_to_apply_pwm():
    backend,port=hardware()
    try:
        with pytest.raises(RobotActionError):backend.teleop_start('forward',pwm_percent=40)
        assert port.sent==['t'] and not backend.snapshot()['pwm_manual_available']
    finally:backend.close()

def test_controller_forwards_pwm_updates_and_release(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        desk.connect(CLIENT,'DEMO');wait_job(desk)
        desk.teleop_start(CLIENT,'backward',pwm_percent=40)
        desk.teleop_pulse(CLIENT,pwm_percent=100)
        assert desk.snapshot()['holding']['pwm_percent']==100
        assert desk.backend.snapshot()['manual_pwm_actual']=={'A':100,'B':100}
        desk.teleop_release(CLIENT)
        assert desk.backend.stopped and not desk.holding
    finally:desk.close()
