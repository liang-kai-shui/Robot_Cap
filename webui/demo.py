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
            self.record('rx', line='[DEMO] VEHICLE_READY: firmware=1.8 protocol=PARAM1 front_guard=OFF')
        return self

    def sample(self):
        if not self.connected or time.monotonic()-self._last_sample < .5:
            return
        self._last_sample = time.monotonic()
        self.record('rx', line='[DEMO] BMI ready=1 cal=250/250 calibrated=1 fresh=1 error=0 norm_g=1.002 roll=0.6 pitch=-1.3 tilt_valid=1 angle_valid=1')
        self.record('rx', line=f'[DEMO] BODY_YAW angle_deg={self.yaw:.1f} rate_dps=0.03 valid=1 level=1 tilt_deg=1.4 zero_pending=0')
        self.record('rx', line='[DEMO] TOF DISABLED front_guard=OFF')

    def snapshot(self):
        with self.lock:
            return {'connected': self.connected, 'ready': self.ready, 'stopped': self.stopped,
                    'firmware': '1.8' if self.connected else None, 'front_guard': 'OFF' if self.connected else None,
                    'pose_available': False, 'range_available': False, 'collision': None,
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
            self.record('rx', line='[DEMO] VEHICLE_READY: firmware=1.8 protocol=PARAM1 front_guard=OFF')
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
            for direction, number, unit in re.findall(r'(前进|左转|右转)\s*(\d+(?:\.\d+)?)\s*(厘米|米|度)', task):
                number = float(number)
                if direction == '前进' and unit in ('厘米', '米'):
                    actions.append(f'robot.move({number/100 if unit == "厘米" else number})')
                elif direction in ('左转', '右转') and unit == '度':
                    actions.append(f'robot.turn({number if direction == "左转" else -number})')
            reply = '\n'.join(actions+['robot.stop()']) if actions else 'REFUSE: 演示模式请使用明确的前进厘米数或左右转角度'
        return LLMResponse(reply, '离线演示（未调用模型）', None, None, None, 0)
