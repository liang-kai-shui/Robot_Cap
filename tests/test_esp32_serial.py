"""Serial transcripts and threaded Runtime/Worker tests; never open hardware."""
from dataclasses import replace
import json
import queue
import threading
import time

import pytest

from agent.prompts import generate_robot_api_prompt
from robot.backends.esp32_serial import ESP32SerialBackend, build_esp32_registry
from robot.runtime import RobotRuntime
from robot.state import Pose
from runtime.errors import (ActionLimitExceeded, RobotActionError,
                            RobotActionTimeoutError, RobotBackendError,
                            RobotCommunicationError, RobotEmergencyStopError,
                            UnsafePolicyError)
from runtime.executor import PolicyExecutor
from runtime.limits import RuntimeLimits
from runtime.validator import PolicyValidator


READY = 'VEHICLE_READY: firmware=1.8 protocol=PARAM1 front_guard=OFF; W/F/J/L\n'
ACK = 'VEHICLE_STOP source=T was_running=0; no queued motion.\n'
F_START = 'VEHICLE_START action=f DISTANCE_MOVE target_mm=300\n'
F_STOP = 'VEHICLE_STOP action=f source=DISTANCE_TARGET\n'
F_RESULT = ('DISTANCE_RESULT action=f target_mm=300 cutoff_right_mm=298.5 cutoff_left_mm=301.8 '
            'after_stop_right_mm=315.2 after_stop_left_mm=317.0 after_stop_mean_mm=316.1 '
            'encoder_valid=1 yaw_valid=1 cutoff_yaw_error_deg=0.53 after_stop_yaw_error_deg=0.57 elapsed_ms=2634.6\n')
F_DONE = 'VEHICLE_DONE action=f type=DISTANCE_MOVE; motors off.\n'
REVERSE_START = 'VEHICLE_START action=s right_sign=-1 left_sign=-1 target=2200cps initial=40% cap=60% duration=800ms\n'
REVERSE_A = 'VEHICLE_RESULT action=s motor=A side=RIGHT vehicle_sign=-1 tail_cps=2200 count_delta=1760 edges=1760\n'
REVERSE_B = 'VEHICLE_RESULT action=s motor=B side=LEFT vehicle_sign=-1 tail_cps=2200 count_delta=-1760 edges=1760\n'
REVERSE_DONE = 'VEHICLE_DONE elapsed_ms=804.2 samples=38 both_tracking=YES tail_speed_difference=0.0cps\n'
REVERSE = REVERSE_START+REVERSE_A+REVERSE_B+REVERSE_DONE+READY


def transcript(key):
    if key.startswith('@F'):
        mm = int(key[2:].strip())
        return (f'VEHICLE_START action=f DISTANCE_MOVE target_mm={mm}\n'+F_STOP+
                f'DISTANCE_RESULT action=f target_mm={mm} cutoff_right_mm={mm-1} cutoff_left_mm={mm+1} '
                f'after_stop_right_mm={mm+12} after_stop_left_mm={mm+14} after_stop_mean_mm={mm+13} '
                'encoder_valid=1 yaw_valid=1 cutoff_yaw_error_deg=0.2 after_stop_yaw_error_deg=0.3 elapsed_ms=1700\n'+F_DONE+READY)
    if key == 'f':
        return F_START+F_STOP+F_RESULT+F_DONE+READY
    degrees = 90
    if key.startswith(('@J', '@L')):
        degrees = int(key[2:].strip())/10
        key = key[1].lower()
    sign = 1 if key == 'j' else -1
    return (f'VEHICLE_START action={key} ANGLE_TURN target_deg={sign*degrees}\n'
            f'VEHICLE_STOP action={key} source=ANGLE_TARGET\n'
            f'TURN_RESULT action={key} target_deg={sign*degrees} cutoff_deg={sign*(degrees-1.8)} '
            f'after_stop_deg={sign*(degrees+.3)} error_deg={sign*.3} valid=1 within5deg=YES elapsed_ms=1404.9\n'
            f'VEHICLE_DONE action={key} type=ANGLE_TURN; motors off.\n'+READY)


class FakePort:
    def __init__(self, *, responses=None, handshake=ACK+READY, chunks=False):
        self.is_open = False
        self.incoming = queue.Queue()
        self.sent = []
        self.responses = responses or {}
        self.handshake = handshake
        self.chunks = chunks
        self.reset_called = False

    def open(self):
        assert self.dtr is False and self.rts is False
        assert self.baudrate == 115200 and self.timeout == .02 and self.write_timeout == .2
        self.is_open = True

    def reset_input_buffer(self):
        self.reset_called = True
        while not self.incoming.empty():
            self.incoming.get_nowait()

    def feed(self, text):
        data = text.encode() if isinstance(text, str) else text
        if self.chunks:
            for n in range(0, len(data), 13):
                self.incoming.put(data[n:n+13])
        else:
            self.incoming.put(data)

    def write(self, data):
        assert self.is_open and self.reset_called
        key = data.decode()
        self.sent.append(key)
        self.feed(self.handshake if key == 't' else self.responses.get(key, transcript(key)))
        return len(data)

    def read(self, size):
        try:
            item = self.incoming.get(timeout=.01)
        except queue.Empty:
            return b''
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.is_open = False


def connected(**kwargs):
    port = FakePort(**kwargs)
    backend = ESP32SerialBackend(serial_factory=lambda: port)
    backend.connect(timeout=.5)
    return backend, port


def test_real_transcript_requires_completion_and_stopped_ready():
    backend, port = connected(chunks=True)
    try:
        result = backend.move(.30)
        assert result['after_stop_mean_mm'] == 316.1
        assert result['measurement'] == 'encoder_estimate_not_ground_pose'
        assert backend.snapshot()['ready'] and backend.snapshot()['stopped']
        result = backend.turn(90)
        assert result['after_stop_deg'] == 90.3
        assert backend.stop()['wheel_standstill_confirmed']
        assert port.sent == ['t', 'f', 'j', 't']
        assert not backend.snapshot()['pose_available']
        assert not backend.snapshot()['range_available']
        assert backend.snapshot()['collision'] is None
    finally:
        backend.close()
    assert port.sent[-1] == 't' and not port.is_open


def test_right_turn_uses_l_and_negative_feedback():
    backend, port = connected()
    try:
        assert backend.turn(-90)['after_stop_deg'] == -90.3
        assert port.sent == ['t', 'l']
    finally:
        backend.close()


@pytest.mark.parametrize('kind,value,wire,target', [
    ('move', .05, '@F50\n', 50), ('move', .20, '@F200\n', 200),
    ('move', .299, '@F299\n', 299), ('turn', 15, '@J150\n', 15),
    ('turn', 45, '@J450\n', 45), ('turn', -15.1, '@L151\n', -15.1),
    ('turn', -89.9, '@L899\n', -89.9),
])
def test_parameter_frame_target_and_phase_timings(kind, value, wire, target):
    backend, port = connected(chunks=True)
    try:
        result = getattr(backend, kind)(value)
        assert port.sent == ['t', wire]
        assert result['target_mm' if kind == 'move' else 'target_deg'] == target
        timings = result['timings_ms']
        assert set(timings) == {'serial_start_wait', 'host_start_to_cutoff', 'firmware_drive',
                               'cutoff_to_report', 'report_to_done', 'done_to_stopped_ready', 'action_total'}
        assert all(value >= 0 for value in timings.values())
        assert timings['firmware_drive'] == result['elapsed_ms']
        assert timings['action_total'] == pytest.approx(sum(value for name, value in timings.items()
                    if name not in ('action_total', 'firmware_drive')))
        stopped = backend.stop()['timings_ms']
        assert stopped['serial_stop_ack_wait'] >= 0 and stopped['ack_to_stopped_ready'] >= 0
        assert stopped['action_total'] >= stopped['serial_stop_ack_wait']+stopped['ack_to_stopped_ready']
    finally:
        backend.close()


@pytest.mark.parametrize('reply', [transcript('f'), transcript('@F200\n').replace('target_mm=200', 'target_mm=300')])
def test_parameter_target_mismatch_stops_without_falling_back_to_legacy(reply):
    backend, port = connected(responses={'@F200\n': reply})
    try:
        with pytest.raises(RobotBackendError):
            backend.move(.2)
        assert port.sent == ['t', '@F200\n', 't']
    finally:
        backend.close()


def test_console_colours_do_not_break_target_or_elapsed_parsing():
    backend, _ = connected(responses={'@J450\n': '\x1b[32m'+transcript('@J450\n').replace('\n', '\x1b[0m\n')})
    try:
        assert backend.turn(45)['timings_ms']['firmware_drive'] == 1404.9
        assert any('\x1b' in item.get('line', '') for item in backend.logs)
    finally:
        backend.close()


def test_timings_measure_waits_and_do_not_replace_them_with_firmware_elapsed():
    wire = '@F200\n'
    lines = transcript(wire).splitlines(keepends=True)
    backend, port = connected(responses={wire: lines[0]})
    def deliver():
        # Wait for each received marker before delaying the next one: USB-like
        # buffering/thread scheduling must not accidentally merge test phases.
        for marker, line in zip(('started', 'cutoff_at', 'result_at', 'done_at'), lines[1:]):
            with backend._condition:
                assert backend._condition.wait_for(
                    lambda: backend._active and getattr(backend._active, marker), timeout=1)
            time.sleep(.025)
            port.feed(line)
    thread = threading.Thread(target=deliver, daemon=True)
    try:
        thread.start()
        timings = backend.move(.2)['timings_ms']
        assert timings['action_total'] >= 75
        assert timings['host_start_to_cutoff'] >= 15
        assert timings['cutoff_to_report'] >= 15
        assert timings['done_to_stopped_ready'] >= 15
        assert timings['firmware_drive'] == 1700  # Separate clock supplied by firmware.
    finally:
        thread.join(1)
        backend.close()


@pytest.mark.parametrize('value', [-.3, 0, .301, 1, True, float('nan'), float('inf'), '0.3'])
def test_unsupported_move_is_rejected_before_transport(value):
    backend = ESP32SerialBackend()
    with pytest.raises(RobotActionError):
        backend.move(value)
    assert backend.logs == []


@pytest.mark.parametrize('value', [0, 5, 180, True, float('nan'), '90'])
def test_unsupported_turn_is_rejected_before_transport(value):
    backend = ESP32SerialBackend()
    with pytest.raises(RobotActionError):
        backend.turn(value)


def test_speed_pose_and_range_are_not_fabricated():
    backend = ESP32SerialBackend()
    for call in (lambda: backend.move(.3, .1), lambda: backend.turn(90, 45), backend.get_pose, backend.get_distance):
        with pytest.raises(RobotActionError):
            call()
    registry = build_esp32_registry(backend)
    prompt = generate_robot_api_prompt(registry)
    assert 'get_distance' not in prompt and 'get_pose' not in prompt
    assert 'distance=0.05..0.30' in prompt and 'obstacle guard OFF' in prompt
    for policy in ('robot.get_distance()', 'robot.get_pose()', 'robot.move(.3, speed=.1)'):
        with pytest.raises(UnsafePolicyError):
            PolicyValidator(registry).validate(policy)


@pytest.mark.parametrize('handshake', [
    ACK+READY.replace('1.8', '1.7'), ACK+READY.replace('OFF', 'ON'), ACK+READY.replace('PARAM1', 'OTHER'),
    READY, ACK, READY+ACK,
])
def test_fresh_stop_ack_and_compatible_ready_required(handshake):
    port = FakePort(handshake=handshake)
    backend = ESP32SerialBackend(serial_factory=lambda: port)
    with pytest.raises((RobotBackendError, RobotActionTimeoutError)):
        backend.connect(timeout=.08)
    assert all(key == 't' for key in port.sent) and not port.is_open


@pytest.mark.parametrize('reply', [
    F_RESULT+F_DONE+READY,
    F_START+F_RESULT+F_DONE+READY,
    F_START+F_STOP+F_DONE+READY,
    F_START+F_STOP+F_RESULT+F_DONE.replace('action=f', 'action=j')+READY,
    F_START+F_STOP+F_RESULT+F_DONE.replace('DISTANCE_MOVE', 'ANGLE_TURN')+READY,
    F_START+READY+F_STOP+F_RESULT+F_DONE,
    F_START+F_START,
    F_START.replace('action=f', 'action=j'),
    F_START+'STOP reason=ENCODER_NO_EDGE\n',
    'VEHICLE_BLOCKED action=f cause=BMI_INVALID\n',
    F_START+'ANGLE_INVALID cause=SAMPLE_GAP\n',
    F_START+'VEHICLE_STOP source=BOOT was_running=1;\n'+READY,
    F_START+'KEYBOARD_DRIVE v1.7\n',
])
def test_out_of_order_fault_stop_or_reset_cancels_remaining_actions(reply):
    backend, port = connected(responses={'f': reply})
    try:
        with pytest.raises((RobotBackendError, RobotCommunicationError, RobotEmergencyStopError)):
            backend.move(.3)
        with pytest.raises((RobotBackendError, RobotCommunicationError, RobotEmergencyStopError)):
            backend.turn(90)
        assert 'j' not in port.sent and port.sent.count('f') == 1 and port.sent[-1] == 't'
    finally:
        backend.close()


@pytest.mark.parametrize('result', [
    F_RESULT.replace('encoder_valid=1', 'encoder_valid=0'),
    F_RESULT.replace('yaw_valid=1', 'yaw_valid=0'),
    F_RESULT.replace('target_mm=300', 'target_mm=301'),
    F_RESULT.replace('elapsed_ms=2634.6', 'elapsed_ms=7000'),
    F_RESULT.replace('after_stop_mean_mm=316.1', 'after_stop_mean_mm=nan'),
    F_RESULT.replace('after_stop_mean_mm=316.1', 'after_stop_mean_mm=310.0'),
])
def test_invalid_distance_result_is_never_success(result):
    backend, port = connected(responses={'f': F_START+F_STOP+result+F_DONE+READY})
    try:
        with pytest.raises(RobotBackendError):
            backend.move(.3)
        assert port.sent.count('f') == 1
    finally:
        backend.close()


@pytest.mark.parametrize('reply', [
    transcript('j').replace('valid=1', 'valid=0'),
    transcript('j').replace('within5deg=YES', 'within5deg=NO'),
    transcript('j').replace('after_stop_deg=90.3', 'after_stop_deg=100'),
    transcript('j').replace('error_deg=0.3', 'error_deg=2.0'),
    transcript('j').replace('target_deg=90', 'target_deg=-90'),
])
def test_turn_feedback_direction_and_quality_checked(reply):
    backend, _ = connected(responses={'j': reply})
    try:
        with pytest.raises(RobotBackendError):
            backend.turn(90)
    finally:
        backend.close()


def test_done_without_new_ready_times_out_and_never_retries():
    backend, port = connected(responses={'f': F_START+F_STOP+F_RESULT+F_DONE})
    # Disable subsequent STOP READY to prove completion cannot be inferred.
    port.handshake = ACK
    try:
        with pytest.raises(RobotActionTimeoutError):
            backend.move(.3, deadline=time.monotonic()+.08)
        assert port.sent.count('f') == 1
        assert backend.snapshot()['stopped'] is None
    finally:
        backend.close()


def test_disconnect_does_not_claim_stop_or_retry():
    backend, port = connected(responses={'f': F_START})
    port.incoming.put(OSError('USB removed'))
    try:
        with pytest.raises(RobotCommunicationError):
            backend.move(.3)
        assert backend.snapshot()['connected'] is False
        assert backend.snapshot()['stopped'] is not True
        assert port.sent.count('f') <= 1
    finally:
        backend.close()


def test_concurrent_emergency_stop_interrupts_wait_and_prevents_turn():
    backend, port = connected(responses={'f': F_START})
    errors = []
    def move():
        try:
            backend.move(.3)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=move)
    thread.start()
    try:
        end = time.monotonic()+1
        while 'f' not in port.sent and time.monotonic() < end:
            time.sleep(.002)
        assert 'f' in port.sent
        with pytest.raises(RobotActionError, match='already active'):
            backend.turn(90)
        backend.emergency_stop()
        thread.join(.5)
        assert not thread.is_alive() and isinstance(errors[0], RobotEmergencyStopError)
        with pytest.raises(RobotEmergencyStopError):
            backend.turn(90)
        assert 'j' not in port.sent and port.sent[-1] == 't'
    finally:
        backend.close()
        thread.join(.5)


def test_pre_cancelled_action_sends_no_motion():
    backend, port = connected()
    cancelled = threading.Event()
    cancelled.set()
    try:
        with pytest.raises(RobotEmergencyStopError):
            backend.move(.3, cancel_event=cancelled)
        assert 'f' not in port.sent
    finally:
        backend.close()


@pytest.mark.parametrize('line', ['VEHICLE_STOP source=BOOT was_running=0;\n', ACK])
def test_external_stop_even_while_idle_latches_motion_permission(line):
    backend, port = connected()
    try:
        backend._on_line(line)
        with pytest.raises(RobotEmergencyStopError):
            backend.move(.3)
        assert 'f' not in port.sent
    finally:
        backend.close()


def test_stop_between_ready_check_and_motion_write_never_sends_motion(monkeypatch):
    backend, port = connected()
    wait = backend._wait
    def interleaved_wait(*args):
        wait(*args)
        backend._on_line('VEHICLE_STOP source=BOOT was_running=0;\n')
    monkeypatch.setattr(backend, '_wait', interleaved_wait)
    try:
        with pytest.raises(RobotEmergencyStopError):
            backend.move(.3)
        assert 'f' not in port.sent
    finally:
        backend.close()


def test_action_budget_rejects_extra_motion_but_stop_remains_callable():
    backend, port = connected()
    backend.max_actions = 1
    try:
        backend.move(.3)
        with pytest.raises(ActionLimitExceeded):
            backend.turn(90)
        assert backend.stop()['stop_acknowledged']
        assert 'j' not in port.sent
    finally:
        backend.close()


def test_runtime_uses_hardware_registry_and_deduplicates_commands():
    backend, port = connected()
    registry = build_esp32_registry(backend)
    runtime = RobotRuntime(backend, registry=registry)
    try:
        request = {'command_id': 1, 'action': 'move', 'args': [.30], 'kwargs': {}}
        result = runtime.dispatch(request)
        assert result['ok'] and result == runtime.dispatch(request)
        assert port.sent.count('f') == 1
        assert not runtime.dispatch({'command_id': 2, 'action': 'move', 'args': [1], 'kwargs': {}})['ok']
        assert not runtime.dispatch({'command_id': 3, 'action': 'get_distance', 'args': [], 'kwargs': {}})['ok']
        state = runtime.dispatch({'command_id': 4, 'action': 'get_state', 'args': [], 'kwargs': {}})
        assert state['ok'] and state['result']['pose_available'] is False
        json.dumps(runtime.trace, allow_nan=False)
    finally:
        backend.close()


def test_runtime_timeout_requests_stop_without_waiting_for_motion_lock():
    backend, port = connected(responses={'f': F_START})
    registry = build_esp32_registry(backend)
    item = registry.get('motion.move')
    registry.unregister('motion.move')
    registry.register(replace(item.spec, timeout_seconds=.05), item.handler, item.validator, backend.emergency_stop)
    runtime = RobotRuntime(backend, registry=registry)
    try:
        response = runtime.dispatch({'command_id': 1, 'action': 'move', 'args': [.3], 'kwargs': {}})
        assert response['error_type'] == 'RobotActionTimeoutError'
        assert port.sent[0:2] == ['t', 'f'] and port.sent[-1] == 't'
        assert any(event['kind'] == 'safety' for event in runtime.trace)
    finally:
        backend.close()


def test_stricter_runtime_budget_is_honored_by_hardware_registry():
    backend, port = connected()
    runtime = RobotRuntime(backend, RuntimeLimits(max_actions=1), build_esp32_registry(backend))
    try:
        assert runtime.dispatch({'command_id': 1, 'action': 'move', 'args': [.3], 'kwargs': {}})['ok']
        result = runtime.dispatch({'command_id': 2, 'action': 'turn', 'args': [90], 'kwargs': {}})
        assert result['error_type'] == 'ActionLimitExceeded' and 'j' not in port.sent
        assert runtime.dispatch({'command_id': 3, 'action': 'stop', 'args': [], 'kwargs': {}})['ok']
    finally:
        backend.close()


def test_isolated_policy_worker_controls_parent_serial_backend():
    backend, port = connected()
    limits = RuntimeLimits(max_actions=3)
    runtime = RobotRuntime(backend, limits, build_esp32_registry(backend))
    try:
        outcome = PolicyExecutor(limits).execute('robot.move(.30)\nrobot.turn(90)\nrobot.stop()',
                                                 None, Pose(0, 0, 0), runtime=runtime)
        assert outcome.success, outcome.error_message
        assert port.sent == ['t', 'f', 'j', 't']
        assert isinstance(outcome.final_state, dict) and outcome.final_state['stopped'] is True
        assert 'pose' not in outcome.final_state
        assert len([e for e in outcome.trace if e['phase'] == 'completed']) == 3
    finally:
        backend.close()


def test_worker_stops_policy_after_failed_first_hardware_action():
    backend, port = connected(responses={'f': 'VEHICLE_BLOCKED action=f cause=BMI_INVALID\n'})
    runtime = RobotRuntime(backend, registry=build_esp32_registry(backend))
    try:
        outcome = PolicyExecutor().execute('robot.move(.30)\nrobot.turn(90)',
                                           None, Pose(0, 0, 0), runtime=runtime)
        assert not outcome.success and outcome.error_type == 'RobotBackendError'
        assert 'j' not in port.sent and port.sent[-1] == 't'
    finally:
        backend.close()


def test_manual_reverse_uses_legacy_s_and_both_encoders_then_stopped_ready():
    backend, port = connected(responses={'s': REVERSE}, chunks=True)
    try:
        result = backend.reverse()
        assert port.sent == ['t', 's']
        assert result['duration_ms'] == 800 and result['encoder_valid'] is True
        assert result['measurement'] == 'timed_reverse_not_distance_control'
        assert backend.snapshot()['stopped'] and backend.snapshot()['ready']
        assert 'reverse' not in generate_robot_api_prompt(build_esp32_registry(backend))
    finally:
        backend.close()


@pytest.mark.parametrize('transcript', [
    REVERSE.replace('duration=800ms', 'duration=700ms'),
    REVERSE.replace('right_sign=-1', 'right_sign=1'),
    REVERSE.replace(REVERSE_B, ''),
    REVERSE.replace(REVERSE_B, REVERSE_A),
    REVERSE.replace('count_delta=-1760', 'count_delta=1760'),
    REVERSE.replace('elapsed_ms=804.2', 'elapsed_ms=1500'),
    REVERSE.replace(REVERSE_DONE, ''),
])
def test_reverse_rejects_mismatched_or_missing_feedback_without_retry(transcript):
    backend, port = connected(responses={'s':transcript})
    try:
        with pytest.raises(RobotBackendError): backend.reverse()
        assert port.sent.count('s') == 1 and port.sent[-1] == 't'
        assert not backend.snapshot()['ready']
    finally: backend.close()


def test_reverse_missing_ready_times_out_and_requests_stop():
    backend, port = connected(responses={'s':REVERSE.removesuffix(READY)})
    try:
        with pytest.raises(RobotActionTimeoutError): backend.reverse(deadline=time.monotonic()+.08)
        assert port.sent.count('s') == 1 and port.sent[-1] == 't'
    finally: backend.close()
