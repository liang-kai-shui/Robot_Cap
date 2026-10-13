"""Trusted differential mixing for a finite, normalized manual joystick."""
import math
from runtime.errors import RobotActionError

def mix_joystick(x, y, limit):
    if type(limit) is not int or not 0 <= limit <= 100:
        raise RobotActionError('摇杆 PWM 上限必须为0–100整数')
    for value in (x,y):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not -1<=value<=1:
            raise RobotActionError('摇杆坐标必须为−1到1的有限数值')
    right,left=-y-x,-y+x
    scale=max(1,abs(right),abs(left))
    def pwm(value):
        value=value/scale*limit
        return (1 if value>=0 else -1)*math.floor(abs(value)+.5)
    return pwm(right),pwm(left)

def validate_wheels(right,left):
    if any(type(v) is not int or not -100<=v<=100 for v in (right,left)):
        raise RobotActionError('左右轮 PWM 必须为−100到100整数')

def vector_wire(session,right,left):
    validate_wheels(right,left)
    if type(session) is not int or not 1<=session<=9999:
        raise RobotActionError('摇杆会话编号已耗尽，请停止并复位')
    return f'@V{session*100000+(right+100)*201+left+100}\n'
