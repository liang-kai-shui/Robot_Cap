import math
import pytest
from robot.manual_control import mix_joystick,vector_wire
from robot.backends.esp32_serial import ESP32SerialBackend
from runtime.errors import RobotActionError
from test_esp32_serial import FakePort,ACK
from test_manual_pwm import PWM_READY
from test_webui import CLIENT,wait_job
from webui.controller import ControlDesk

READY=PWM_READY.replace('pwm-stream-v1','pwm-stream-v6').strip()+' teleop_vector=1\n'

@pytest.mark.parametrize('x,y,expected',[(0,-1,(40,40)),(0,1,(-40,-40)),(1,0,(-40,40)),(-1,0,(40,-40)),(.5,-.5,(0,40)),(-.5,-.5,(40,0)),(0,-.5,(20,20)),(0,0,(0,0))])
def test_differential_mix(x,y,expected):
    assert mix_joystick(x,y,40)==expected

def test_all_angles_and_limits_preserve_direction_and_bound():
    for angle in range(360):
        x,y=math.cos(math.radians(angle)),math.sin(math.radians(angle))
        for limit in (0,1,40,100):
            right,left=mix_joystick(x,y,limit)
            assert max(abs(right),abs(left))<=limit
            encoded=int(vector_wire(9999,right,left)[2:])
            assert encoded//100000==9999
            payload=encoded%100000
            assert (payload//201-100,payload%201-100)==(right,left)

def test_small_stick_quantization_is_symmetric():
    assert mix_joystick(0,-.0125,40)==(1,1)
    assert mix_joystick(0,.0125,40)==(-1,-1)
    assert mix_joystick(0,-.01,40)==(0,0)

@pytest.mark.parametrize('x,y,limit',[(True,0,40),(math.nan,0,40),(0,math.inf,40),(1.01,0,40),(0,0,True),(0,0,101),(0,'0',40)])
def test_bad_vector_rejected(x,y,limit):
    with pytest.raises(RobotActionError):mix_joystick(x,y,limit)

def test_continuous_updates_same_session_and_neutral_never_resumes():
    port=FakePort(handshake=ACK+READY)
    backend=ESP32SerialBackend(serial_factory=lambda:port);backend.connect(timeout=.5)
    try:
        backend.teleop_start('joystick',wheels=(40,40))
        backend.teleop_pulse(wheels=(15,40))
        backend.teleop_pulse(wheels=(-40,40))
        assert port.sent==['t',vector_wire(1,40,40),vector_wire(1,15,40),vector_wire(1,-40,40)]
        port.handshake=ACK+READY.strip()+' teleop_next=2\n'
        result=backend.teleop_pulse(wheels=(0,0));assert not result['holding'] and port.sent[-1]=='t'
        before=list(port.sent)
        with backend._condition:
            assert backend._condition.wait_for(lambda:backend.snapshot()['ready'],timeout=.5)
        assert backend.teleop_pulse(wheels=(40,40))['holding'] is False
        assert port.sent==before
        backend.teleop_start('joystick',wheels=(40,40))
        assert port.sent[-1]==vector_wire(2,40,40)
    finally:backend.close()

def test_old_firmware_rejects_joystick_without_motion():
    port=FakePort(handshake=ACK+PWM_READY);backend=ESP32SerialBackend(serial_factory=lambda:port)
    backend.connect(timeout=.5)
    try:
        with pytest.raises(RobotActionError,match='v6'):backend.teleop_start('joystick',wheels=(40,40))
        assert port.sent==['t']
    finally:backend.close()

def test_controller_joystick_owner_mixing_and_stop(tmp_path):
    desk=ControlDesk(demo=True,log_dir=tmp_path,monitor=False)
    try:
        desk.connect(CLIENT,'DEMO');wait_job(desk)
        desk.joystick_start(CLIENT,.5,-.5,40)
        assert desk.backend.vector_wheels==(0,40)
        desk.joystick_pulse(CLIENT,1,0,60)
        assert desk.backend.vector_wheels==(-60,60)
        with pytest.raises(ValueError):desk.joystick_pulse('another-client',0,-1,40)
        assert desk.backend.vector_wheels==(-60,60)
        with pytest.raises(ValueError):desk.teleop_pulse(CLIENT,pwm_percent=40)
        result=desk.joystick_pulse(CLIENT,0,0,40)
        assert not result['holding'] and desk.holding is None and desk.backend.stopped
    finally:desk.close()
