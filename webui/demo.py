"""Explicitly labelled offline UI demo; never opens a serial port or an API."""
from collections import deque
import math
import re
import threading
import time

from providers.base import LLMResponse
from robot.hardware_limits import move_mm, turn_tenths
from runtime.errors import RobotEmergencyStopError


class DemoBackend:
    def __init__(self, port='DEMO', **_kwargs):
        self.port_name = port
        self.lock = threading.RLock()
        self._logs = deque(maxlen=4096)
        self.connected = self.ready = False
        self.stopped = None
        self.fault = None
        self.action_count = 0
        self.last_action = self.last_result = None
        self.yaw = 22.8
        self._last_sample = 0
        self.range_mm=600
        self.range_offset=0
        self.teleop_direction=None
        self.teleop_session=0
        self._teleop_renewed=0
        self._range=None

    def record(self, kind, **details):
        with self.lock:
            self._logs.append({'kind': kind, 'time_monotonic': time.monotonic(), **details})

    @property
    def logs(self):
        with self.lock:
            return list(self._logs)

    def connect(self, **_kwargs):
        with self.lock:
            self.connected = self.ready = self.stopped = True
            self.record('rx', line='[DEMO] VEHICLE_READY: firmware=1.9 protocol=PARAM2 boot_id=DEMOBOOT front_guard=OFF tof=ON')
        return self

    def sample(self):
        if not self.connected or time.monotonic()-self._last_sample < .5:
            return
        self._last_sample = time.monotonic()
        self.record('rx', line='[DEMO] BMI ready=1 cal=250/250 calibrated=1 fresh=1 error=0 norm_g=1.002 roll=0.6 pitch=-1.3 tilt_valid=1 angle_valid=1')
        self.record('rx', line=f'[DEMO] BODY_YAW angle_deg={self.yaw:.1f} rate_dps=0.03 valid=1 level=1 tilt_deg=1.4 zero_pending=0')
        if self.teleop_direction and time.monotonic()-self._teleop_renewed>.25: self.teleop_release()
        self._range={'_at':time.monotonic()}
        self.record('rx',line=f'[DEMO] TOF ready=1 distance_mm={self.range_mm} valid=1 status=0 error=0 age_ms=0')
        self.record('rx',line=f'[DEMO] ATTITUDE roll=0.6 pitch=-1.3 yaw={self.yaw:.1f} tilt_valid=1 yaw_valid=1 calibrated=1 age_ms=0 zero_epoch=0')
        velocity=100 if self.teleop_direction=='forward' else -100 if self.teleop_direction=='backward' else 0
        for motor,side in [('A','RIGHT'),('B','LEFT')]:
            self.record('rx',line=f'[DEMO] WHEEL motor={motor} side={side} count=0 forward_count=0 speed_mm_s={velocity} cps={velocity*19.2} rpm={velocity*.44415} valid=1 dt_ms=250 age_ms=0')

    def snapshot(self):
        with self.lock:
            return {'connected': self.connected, 'ready': self.ready, 'stopped': self.stopped,
                    'firmware': '1.9' if self.connected else None, 'front_guard': 'OFF' if self.connected else None,
                    'pose_available': False, 'range_available': True, 'boot_id':'DEMOBOOT', 'teleop':bool(self.teleop_direction), 'collision': None,
                    'last_action': self.last_action, 'last_result': self.last_result,
                    'action_count': self.action_count, 'fault': self.fault}

    def begin_task(self):
        with self.lock:
            if self.fault or not self.ready or not self.connected:
                raise RobotEmergencyStopError('演示会话需要重新连接')
            self.action_count = 0

    def _motion(self, action, result, cancel_event):
        with self.lock:
            self.begin_task_check()
            self.ready = self.stopped = False
            self.last_action = action
            self.action_count += 1
            self.record('tx', command=action)
        for _ in range(8):
            time.sleep(.035)
            with self.lock:
                if self.fault or not self.connected or (cancel_event and cancel_event.is_set()):
                    raise RobotEmergencyStopError('演示动作已中止')
        with self.lock:
            if action in ('j', 'l'):
                self.yaw += result['after_stop_deg']
            self.ready = self.stopped = True
            self.last_result = result
            self.record('rx', line='[DEMO] '+('DISTANCE_RESULT' if action == 'f' else 'TURN_RESULT')+' '+str(result))
            self.record('rx', line='[DEMO] VEHICLE_READY: firmware=1.9 protocol=PARAM2 boot_id=DEMOBOOT front_guard=OFF tof=ON')
            return result

    def begin_task_check(self):
        if self.fault or not self.connected or not self.ready:
            raise RobotEmergencyStopError('演示连接未就绪')

    def move(self, distance, *, cancel_event=None, deadline=None):
        mm = move_mm(distance)
        return self._motion('f', {'action': 'f', 'target_mm': mm, 'cutoff_right_mm': mm+.4,
            'cutoff_left_mm': mm-.4, 'after_stop_right_mm': mm+11.4, 'after_stop_left_mm': mm+10.8,
            'after_stop_mean_mm': mm+11.1, 'after_stop_yaw_error_deg': .3, 'encoder_valid': True,
            'yaw_valid': True, 'elapsed_ms': 900, 'measurement': 'DEMO_encoder_estimate',
            'timings_ms': {'firmware_drive': 900, 'action_total': 1200}}, cancel_event)

    def reverse_distance(self,distance,*,cancel_event=None,deadline=None):
        mm=move_mm(distance)
        return self._motion('b',{'action':'b','target_mm':mm,'cutoff_right_mm':mm,'cutoff_left_mm':mm,
            'after_stop_right_mm':mm+10,'after_stop_left_mm':mm+10,'after_stop_mean_mm':mm+10,
            'after_stop_yaw_error_deg':.3,'encoder_valid':True,'yaw_valid':True,'elapsed_ms':900,
            'measurement':'DEMO_reverse_encoder_estimate','timings_ms':{'firmware_drive':900,'action_total':1200}},cancel_event)

    def approach(self,distance,*,cancel_event=None,deadline=None):
        target=round(distance*1000)
        result=self._motion('r',{'action':'r','target_mm':target,'after_stop_mm':target,'valid':True,
              'corrected_after_stop_mm':target,'elapsed_ms':600,'measurement':'DEMO_front_clearance'},cancel_event)
        self.range_mm=target
        return result

    def get_distance(self,**_kwargs):
        if not self.connected: raise ValueError('Demo disconnected')
        return {'distance_mm':self.range_mm+self.range_offset,'raw_mm':self.range_mm,'offset_mm':self.range_offset,
                'distance_m':(self.range_mm+self.range_offset)/1000,'valid':True,'age_ms':0}

    def get_attitude(self,**_kwargs):
        return {'roll':.6,'pitch':-1.3,'yaw':self.yaw}

    def configure_range_offset(self,offset):
        self.range_offset=offset

    def teleop_start(self,direction):
        self.begin_task_check()
        self.teleop_direction=direction;self.teleop_session+=1
        self.ready=self.stopped=False
        self._teleop_renewed=time.monotonic()
        return {'session':self.teleop_session,'lease_ms':250}

    def teleop_pulse(self):
        if not self.teleop_direction: raise ValueError('Hold expired')
        self._teleop_renewed=time.monotonic()
        return {'firmware_ack_age_ms':0}

    def teleop_release(self):
        self.teleop_direction=None
        self.ready=self.stopped=self.connected and not self.fault

    def turn(self, angle, *, cancel_event=None, deadline=None):
        turn_tenths(angle)
        return self._motion('j' if angle > 0 else 'l', {'action': 'j' if angle > 0 else 'l',
            'target_deg': angle, 'cutoff_deg': angle-math.copysign(1.8, angle),
            'after_stop_deg': angle+math.copysign(.4, angle), 'error_deg': math.copysign(.4, angle),
            'valid': True, 'elapsed_ms': 650, 'measurement': 'DEMO_relative_gyro',
            'timings_ms': {'firmware_drive': 650, 'action_total': 1000}}, cancel_event)

    def reverse(self, duration=.8, *, cancel_event=None, deadline=None):
        if isinstance(duration, bool) or duration != .8:
            raise ValueError('演示后退固定0.8秒')
        return self._motion('s', {'action':'s', 'duration_ms':800, 'elapsed_ms':800,
            'encoder_valid':True, 'measurement':'DEMO_timed_reverse_not_distance_control',
            'timings_ms':{'firmware_drive':800,'action_total':1000}}, cancel_event)

    def stop(self, **_kwargs):
        with self.lock:
            self.record('tx', command='t')
            self.stopped = True
            self.ready = self.connected and not self.fault
            self.action_count += 1
            return {'stop_acknowledged': True, 'wheel_standstill_confirmed': True,
                    'timings_ms': {'action_total': 30}}

    def emergency_stop(self):
        with self.lock:
            self.teleop_direction=None
            self.fault = 'SESSION_STOPPED'
            self.ready = False
            self.stopped = True
            self.record('emergency_stop')

    def get_state(self, **_kwargs):
        return self.snapshot()

    def close(self):
        with self.lock:
            self.connected = self.ready = False
            self.stopped = True


class DemoProvider:
    def generate_policy(self, task, api, state, system=None):
        import json
        context = json.loads(state)
        if 'completed_segment' in context:
            reply = 'CONTINUE'
        elif '转回去' in task:
            reference = (context.get('session_history') or {}).get('turn_back_reference')
            reply = ('\n'.join(f'robot.turn({value})' for value in reference['inverse_turns_deg'])+'\nrobot.stop()'
                     if reference else 'REFUSE: 演示会话没有已完成的转弯参考')
        else:
            actions = []
            for direction, number, unit in re.findall(r'(前进|后退|左转|右转)\s*(\d+(?:\.\d+)?)\s*(厘米|米|度)', task):
                number = float(number)
                if direction in ('前进','后退') and unit in ('厘米', '米'):
                    actions.append(f'robot.{"move" if direction=="前进" else "reverse"}({number/100 if unit == "厘米" else number})')
                elif direction in ('左转', '右转') and unit == '度':
                    actions.append(f'robot.turn({number if direction == "左转" else -number})')
            approach=re.search(r'距离(?:前面|前方)?\s*(\d+(?:\.\d+)?)\s*(厘米|米)',task)
            if approach: actions=[f'robot.approach({float(approach[1])/100 if approach[2]=="厘米" else float(approach[1])})']
            reply = '\n'.join(actions+['robot.stop()']) if actions else 'REFUSE: 演示模式请使用明确的前进厘米数或左右转角度'
        return LLMResponse(reply, '离线演示（未调用模型）', None, None, None, 0)
