"""Parent-owned serial adapter for KEYBOARD_DRIVE v1.8 PARAM1 firmware.

The firmware has no command IDs. This adapter owns the port exclusively, allows
one action at a time, and never retries movement. Only the measured 30cm and
90-degree maximum are retained; unimplemented observations are never fabricated.
"""
from collections import deque
from dataclasses import dataclass, field
import math
import re
import threading
import time

from robot.backends.physical import PhysicalBackend
from robot.capabilities import CapabilityRegistry, CapabilitySpec, ParameterSpec
from robot.hardware_limits import MAX_TASK_ACTIONS, move_mm, turn_tenths
from runtime.errors import (ActionLimitExceeded, RobotActionError,
                            RobotActionTimeoutError, RobotBackendError,
                            RobotCommunicationError, RobotEmergencyStopError)


@dataclass
class _Motion:
    key: str
    kind: str
    target: float
    sent_at: float
    start_at: float = 0
    cutoff_at: float = 0
    result_at: float = 0
    done_at: float = 0
    ready_at: float = 0
    started: bool = False
    target_stop: bool = False
    result: dict | None = None
    done: bool = False
    ready: bool = False
    wheels: dict = field(default_factory=dict)


def _number(fields, name):
    try:
        value = float(fields[name])
    except (KeyError, ValueError) as exc:
        raise RobotBackendError(f"Missing/invalid firmware field: {name}") from exc
    if not math.isfinite(value):
        raise RobotBackendError(f"Nonfinite firmware field: {name}")
    return value


class ESP32SerialBackend(PhysicalBackend):
    """Bounded serial I/O with a reader independent of the motion handler lock.

    A READY after matching START/target STOP/result/DONE is required to complete
    an action. T/BOOT, invalid results, reset, disconnect and timeouts terminate
    the session's motion permissions. Reconnecting is an explicit user action.
    """

    def __init__(self, port="COM5", *, max_actions=MAX_TASK_ACTIONS, serial_factory=None):
        if isinstance(max_actions, bool) or not isinstance(max_actions, int) or max_actions < 1:
            raise ValueError("max_actions must be a positive integer")
        self.port_name, self.max_actions = port, max_actions
        self._factory = serial_factory
        self._port = None
        self._reader = None
        self._closing = threading.Event()
        self._condition = threading.Condition(threading.RLock())
        self._action_lock = threading.Lock()
        self._active = None
        self._established = self._ready = False
        self._failure = None
        self._aborted = False
        self._stopped = None
        self._stop_acks = 0
        self._stop_ack_at = self._stop_ready_at = 0
        self._await_stop = True
        self._firmware = self._guard = None
        self._action_count = 0
        self._last_action = self._last_result = None
        self._logs = deque(maxlen=4096)

    @property
    def logs(self):
        with self._condition:
            return list(self._logs)

    def _record(self, kind, **details):
        self._logs.append({"time_monotonic": time.monotonic(), "kind": kind, **details})

    def _write(self, key):
        # Called under the condition. Port writes are capped at 200ms; this lock
        # never covers waiting for an action, so emergency_stop can always write.
        if self._port is None or not self._port.is_open:
            raise RobotCommunicationError("Serial port is closed")
        try:
            if self._port.write(key.encode("ascii")) != len(key):
                raise OSError("Incomplete serial write")
        except OSError as exc:
            raise RobotCommunicationError(str(exc)) from exc
        self._record("tx", command=key)

    def _request_stop(self):
        self._ready = False
        self._stopped = None  # A write alone does not confirm wheel standstill.
        self._await_stop = True
        self._write("t")

    def _trip(self, error):
        with self._condition:
            if isinstance(error, RobotCommunicationError):
                self._established = False
            if self._failure is None:
                self._failure = error
                self._record("fault", error_type=type(error).__name__, message=str(error))
                try:
                    self._request_stop()
                except RobotCommunicationError:
                    pass
            self._ready = False
            self._condition.notify_all()

    def connect(self, *, timeout=10.0):
        """Open, send STOP, then wait for fresh STOP acknowledgement and READY."""
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if self._port is not None:
            raise RobotCommunicationError("Backend instances connect only once")
        if self._factory is None:
            try:
                from serial import Serial
            except ImportError as exc:
                raise RobotCommunicationError("Install hardware extra: pip install -e '.[hardware]'") from exc
            self._factory = Serial
        try:
            port = self._factory()
            self._port = port
            port.port, port.baudrate = self.port_name, 115200
            port.timeout, port.write_timeout = 0.02, 0.2
            port.dtr = port.rts = False
            port.open()
            port.reset_input_buffer()
            with self._condition:
                self._request_stop()
            self._reader = threading.Thread(target=self._read_loop, name="esp32-serial-reader", daemon=True)
            self._reader.start()
            self._wait(lambda: self._ready and self._stop_acks > 0,
                       time.monotonic()+timeout, None)
            with self._condition:
                self._established = True
            return self
        except BaseException:
            self.close()
            raise

    def _read_loop(self):
        pending = bytearray()
        try:
            while not self._closing.is_set():
                data = self._port.read(4096)
                if not data:
                    self._closing.wait(0.005)
                    continue
                pending.extend(data)
                if len(pending) > 16384:
                    raise RobotCommunicationError("Serial frame buffer overflow")
                while b"\n" in pending:
                    raw, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    if len(raw) > 4096:
                        raise RobotCommunicationError("Serial line too long")
                    self._on_line(raw.decode("utf-8", errors="replace").strip())
        except (OSError, RobotBackendError, RobotCommunicationError) as exc:
            if not self._closing.is_set():
                self._trip(exc if isinstance(exc, (RobotBackendError, RobotCommunicationError))
                           else RobotCommunicationError(str(exc)))

    def _on_line(self, line):
        # ESP-IDF console colour escapes can trail the last numeric field.
        raw_line = line
        line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        fields = dict(re.findall(r"([A-Za-z_][A-Za-z_0-9]*)=([^\s;]+)", line))
        with self._condition:
            self._record("rx", line=raw_line)
            # Reset invalidates ordering and calibration. Never reconnect/retry.
            if self._established and ("KEYBOARD_DRIVE" in line or line.startswith(("ESP-ROM:", "rst:"))):
                self._trip(RobotCommunicationError("ESP32 reset; reconnect explicitly"))
            if "reason=" in line or "VEHICLE_BLOCKED" in line:
                self._trip(RobotBackendError(line))
            if self._active and "ANGLE_INVALID" in line:
                self._trip(RobotBackendError(line))
            if "VEHICLE_STOP" in line:
                self._ready = False
                self._stopped = None
                if self._established and not self._aborted and (
                        fields.get("source") == "BOOT" or
                        (fields.get("source") == "T" and not self._await_stop)):
                    self._trip(RobotEmergencyStopError(f"External stop cancels this session: {line}"))
                if fields.get("source") == "T":
                    self._stop_acks += 1
                    self._stop_ack_at = time.perf_counter()
                    self._await_stop = False
                motion = self._active
                if motion and self._failure is None and not self._aborted:
                    target = "DISTANCE_TARGET" if motion.key == "f" else "ANGLE_TARGET"
                    if not motion.started or fields.get("action") != motion.key or fields.get("source") != target:
                        self._trip(RobotEmergencyStopError(f"Action interrupted: {line}"))
                    else:
                        motion.target_stop = True
                        motion.cutoff_at = time.perf_counter()
            if "VEHICLE_READY:" in line:
                self._firmware, self._guard = fields.get("firmware"), fields.get("front_guard")
                if self._firmware != "1.8" or fields.get("protocol") != "PARAM1" or self._guard != "OFF":
                    self._trip(RobotBackendError("Requires firmware=1.8 protocol=PARAM1 front_guard=OFF; flash updated firmware first; no movement sent"))
                elif not self._await_stop:
                    motion = self._active
                    if motion and not motion.done and self._failure is None and not self._aborted:
                        self._trip(RobotBackendError("READY received before matching motion completion"))
                    else:
                        self._stopped = True
                        self._stop_ready_at = time.perf_counter()
                        self._ready = self._failure is None and not self._aborted
                        if motion and motion.done:
                            motion.ready = True
                            motion.ready_at = time.perf_counter()
            motion = self._active
            if motion and self._failure is None and not self._aborted:
                if "VEHICLE_START" in line:
                    if motion.started or fields.get("action") != motion.key:
                        self._trip(RobotBackendError("Unexpected/duplicate VEHICLE_START"))
                    else:
                        target_field = "target_mm" if motion.key == "f" else "target_deg"
                        try:
                            value = _number(fields, target_field) if motion.key != 's' else float(fields.get('duration', '').removesuffix('ms'))
                            if not math.isfinite(value) or abs(value-motion.target) > 0.05:
                                raise RobotBackendError("START target differs from requested action")
                            if motion.key == 's' and (fields.get('right_sign') != '-1' or fields.get('left_sign') != '-1'):
                                raise RobotBackendError('Reverse START direction differs from requested action')
                            motion.started = True
                            motion.start_at = time.perf_counter()
                            self._stopped = False
                        except (RobotBackendError, ValueError) as exc:
                            self._trip(exc if isinstance(exc, RobotBackendError) else RobotBackendError(str(exc)))
                if motion.key == 's' and 'VEHICLE_RESULT' in line:
                    try:
                        motor = fields.get('motor')
                        if not motion.started or motor not in ('A', 'B') or motor in motion.wheels:
                            raise RobotBackendError('Reverse result missing START or duplicate motor')
                        if fields.get('action') != 's' or fields.get('vehicle_sign') != '-1' or fields.get('side') != ('RIGHT' if motor == 'A' else 'LEFT'):
                            raise RobotBackendError('Reverse wheel identity/direction mismatch')
                        q, edges, cps = (_number(fields, name) for name in ('count_delta', 'edges', 'tail_cps'))
                        if q * (1 if motor == 'A' else -1) <= 0 or abs(q) > 4000 or edges <= 0 or abs(abs(q)-edges) > 8+edges/100 or not 0 < cps <= 4400:
                            raise RobotBackendError('Invalid reverse encoder feedback')
                        motion.wheels[motor] = {'count_delta': q, 'edges': edges, 'tail_cps': cps}
                        if not motion.cutoff_at:
                            motion.cutoff_at = time.perf_counter()
                    except RobotBackendError as exc:
                        self._trip(exc)
                if "DISTANCE_RESULT" in line or "TURN_RESULT" in line:
                    if not motion.started or not motion.target_stop or motion.result is not None:
                        self._trip(RobotBackendError("Result without ordered START and target STOP"))
                    else:
                        try:
                            motion.result = self._motion_result(motion, fields, line)
                            motion.result_at = time.perf_counter()
                        except RobotBackendError as exc:
                            self._trip(exc)
                if "VEHICLE_DONE" in line:
                    if motion.key == 's':
                        try:
                            elapsed = _number(fields, 'elapsed_ms')
                            if not motion.started or set(motion.wheels) != {'A', 'B'} or motion.done or not 800 <= elapsed <= 900 or _number(fields, 'samples') < 6:
                                raise RobotBackendError('Incomplete or invalid timed reverse completion')
                            motion.result = {'action': 's', 'duration_ms': 800, 'elapsed_ms': elapsed,
                                'encoder_valid': True, 'wheels': dict(motion.wheels), 'measurement': 'timed_reverse_not_distance_control'}
                            motion.result_at = motion.done_at = time.perf_counter()
                            motion.done = True
                        except RobotBackendError as exc:
                            self._trip(exc)
                    elif not motion.started or motion.result is None or motion.done or fields.get("action") != motion.key or fields.get("type") != motion.kind:
                        self._trip(RobotBackendError("Missing/invalid/duplicate action completion"))
                    else:
                        motion.done = True
                        motion.done_at = time.perf_counter()
            elif self._active is None and "VEHICLE_START" in line:
                self._trip(RobotBackendError("Unrequested motion on serial port"))
            self._condition.notify_all()

    @staticmethod
    def _motion_result(motion, fields, line):
        if fields.get("action") != motion.key:
            raise RobotBackendError("Mismatched result action")
        if motion.key == "f":
            if "DISTANCE_RESULT" not in line or fields.get("encoder_valid") != "1" or fields.get("yaw_valid") != "1":
                raise RobotBackendError("Invalid encoder/yaw distance result")
            names = ("target_mm", "cutoff_right_mm", "cutoff_left_mm", "after_stop_right_mm",
                     "after_stop_left_mm", "after_stop_mean_mm", "cutoff_yaw_error_deg",
                     "after_stop_yaw_error_deg", "elapsed_ms")
            result = {name: _number(fields, name) for name in names}
            if abs(result["target_mm"]-motion.target) > 0.05 or not 0 < result["elapsed_ms"] <= 6000:
                raise RobotBackendError("Unexpected distance target/deadline")
            if any(not 0 <= result[name] <= motion.target+100 for name in names[1:6]):
                raise RobotBackendError("Distance feedback out of range")
            if abs((result["after_stop_right_mm"]+result["after_stop_left_mm"])/2-result["after_stop_mean_mm"]) > 0.2:
                raise RobotBackendError("Inconsistent mean distance")
            if (result["cutoff_right_mm"]+result["cutoff_left_mm"])/2 < motion.target-0.2:
                raise RobotBackendError("Distance stopped before requested target")
            result.update(encoder_valid=True, yaw_valid=True, measurement="encoder_estimate_not_ground_pose")
        else:
            if "TURN_RESULT" not in line or fields.get("valid") != "1" or fields.get("within5deg") != "YES":
                raise RobotBackendError("Invalid turn result or error outside 5 degrees")
            names = ("target_deg", "cutoff_deg", "after_stop_deg", "error_deg", "elapsed_ms")
            result = {name: _number(fields, name) for name in names}
            target = motion.target
            if abs(result["target_deg"]-target) > 0.05 or abs(result["after_stop_deg"]-target) > 5 or abs(result["error_deg"]) > 5:
                raise RobotBackendError("Turn feedback outside tested tolerance")
            if abs(result["after_stop_deg"]-target-result["error_deg"]) > 0.2 or not 0 < result["elapsed_ms"] <= 3000:
                raise RobotBackendError("Inconsistent turn feedback/deadline")
            result.update(valid=True, measurement="relative_gyro_angle_not_absolute_heading")
        return {"action": motion.key, **result}

    def _wait(self, predicate, deadline, cancel_event):
        with self._condition:
            while True:
                if self._failure:
                    raise self._failure
                if self._aborted or self._closing.is_set() or (cancel_event is not None and cancel_event.is_set()):
                    self._trip(RobotEmergencyStopError("Serial action cancelled; remaining steps discarded"))
                    raise self._failure
                if time.monotonic() >= deadline:
                    self._trip(RobotActionTimeoutError("ESP32 serial action/handshake timed out; no motion retry"))
                    raise self._failure
                if predicate():
                    return
                self._condition.wait(min(0.02, max(0, deadline-time.monotonic())))

    def _move_action(self, key, target, wire, timeout, cancel_event, deadline):
        if not self._action_lock.acquire(blocking=False):
            raise RobotActionError("An ESP32 action is already active; commands cannot queue")
        end = time.monotonic()+timeout
        if deadline is not None:
            end = min(end, deadline)
        try:
            self._wait(lambda: self._established and self._ready, end, cancel_event)
            with self._condition:
                # Recheck inside the same lock as write, closing the T/START race.
                if self._failure or self._aborted or self._closing.is_set():
                    raise self._failure or RobotEmergencyStopError("Session stopped")
                if not self._ready:
                    raise RobotEmergencyStopError("Readiness lost before motion write")
                if cancel_event is not None and cancel_event.is_set():
                    raise RobotEmergencyStopError("Action cancelled before sending motion")
                if time.monotonic() >= end:
                    raise RobotActionTimeoutError("Action deadline passed before motion")
                if self._action_count >= self.max_actions:
                    raise ActionLimitExceeded("Hardware session action budget exhausted")
                self._action_count += 1
                self._last_action = "REVERSE" if key == 's' else "MOVE" if key == "f" else "TURN"
                self._ready = False
                self._stopped = None
                self._active = _Motion(key, "TIMED_REVERSE" if key == 's' else "DISTANCE_MOVE" if key == "f" else "ANGLE_TURN", target, time.perf_counter())
                self._write(wire)
            self._wait(lambda: self._active.ready, end, cancel_event)
            with self._condition:
                m = self._active
                m.result["timings_ms"] = {
                    "serial_start_wait": (m.start_at-m.sent_at)*1000,
                    "host_start_to_cutoff": (m.cutoff_at-m.start_at)*1000,
                    "firmware_drive": m.result["elapsed_ms"],
                    "cutoff_to_report": (m.result_at-m.cutoff_at)*1000,
                    "report_to_done": (m.done_at-m.result_at)*1000,
                    "done_to_stopped_ready": (m.ready_at-m.done_at)*1000,
                    "action_total": (m.ready_at-m.sent_at)*1000,
                }
                self._last_result = dict(self._active.result)
                return dict(self._last_result)
        except (RobotActionTimeoutError, RobotCommunicationError, RobotBackendError, RobotEmergencyStopError) as exc:
            self._trip(exc)
            raise
        finally:
            with self._condition:
                self._active = None
            self._action_lock.release()

    def move(self, distance, speed=None, *, cancel_event=None, deadline=None):
        mm = move_mm(distance)
        if speed is not None:
            raise RobotActionError("Firmware speed is fixed; custom speed is unavailable")
        return self._move_action("f", mm, "f" if mm == 300 else f"@F{mm}\n", 9.0, cancel_event, deadline)

    def reverse(self, duration=.8, *, cancel_event=None, deadline=None):
        """Trusted manual jog only; legacy S runs 800ms and reports both encoders.

        No policy capability is registered. This is not a requested distance.
        Completion requires both measured wheel results, DONE, and fresh READY.
        """
        if isinstance(duration, bool) or duration != .8:
            raise RobotActionError('Manual reverse duration is fixed at 0.8 seconds')
        return self._move_action('s', 800, 's', 5.0, cancel_event, deadline)

    def begin_task(self):
        """Trusted console boundary: renew only the budget of a healthy idle car.

        This is not a registered policy capability and never clears a fault,
        emergency stop, disconnection or missing standstill confirmation.
        """
        if not self._action_lock.acquire(blocking=False):
            raise RobotActionError("Cannot begin a task while an action is active")
        try:
            with self._condition:
                if self._failure or self._aborted or self._closing.is_set():
                    raise self._failure or RobotEmergencyStopError("Session stopped; restart explicitly")
                if not self._established or not self._port.is_open or not self._ready or self._stopped is not True:
                    raise RobotEmergencyStopError("New task requires connected, healthy, stopped READY")
                if self._active is not None:
                    raise RobotActionError("Cannot begin a task while motion feedback is pending")
                self._record("task_boundary", previous_action_count=self._action_count)
                self._action_count = 0
        finally:
            self._action_lock.release()

    def turn(self, angle, speed=None, *, cancel_event=None, deadline=None):
        tenths = turn_tenths(angle)
        if speed is not None:
            raise RobotActionError("Firmware turn speed is fixed")
        key = "j" if tenths > 0 else "l"
        wire = key if abs(tenths) == 900 else f"@{key.upper()}{abs(tenths)}\n"
        return self._move_action(key, tenths/10, wire, 6.0, cancel_event, deadline)

    def stop(self, *, cancel_event=None, deadline=None):
        """Send STOP independently, then await acknowledgement and stopped READY."""
        started = time.perf_counter()
        with self._condition:
            before = self._stop_acks
            if self._action_lock.locked():
                self._aborted = True
            self._request_stop()
            self._last_action = "STOP"
            self._action_count += 1
            self._condition.notify_all()
        end = min(time.monotonic()+2, deadline) if deadline is not None else time.monotonic()+2
        # STOP is allowed even when a prior error cancelled motion. READY confirms
        # standstill; an acknowledgement alone confirms only drive-off.
        with self._condition:
            while self._stop_acks <= before or self._stopped is not True:
                if self._closing.is_set() or time.monotonic() >= end:
                    raise RobotCommunicationError("STOP acknowledgement/stopped READY missing; wheel state unknown")
                self._condition.wait(0.02)
            timings = {"serial_stop_ack_wait": (self._stop_ack_at-started)*1000,
                       "ack_to_stopped_ready": (self._stop_ready_at-self._stop_ack_at)*1000,
                       "action_total": (time.perf_counter()-started)*1000}
        return {"stop_acknowledged": True, "wheel_standstill_confirmed": self.snapshot()["stopped"] is True,
                "timings_ms": timings}

    def emergency_stop(self):
        """Latch this session against further motion and send T without action lock."""
        with self._condition:
            self._aborted = True
            self._record("emergency_stop")
            try:
                self._request_stop()
            except RobotCommunicationError as exc:
                self._trip(exc)
                raise
            finally:
                self._condition.notify_all()

    def snapshot(self):
        with self._condition:
            return {"connected": self._established and self._port is not None and self._port.is_open and not self._closing.is_set(),
                    "ready": self._ready, "stopped": self._stopped,
                    "firmware": self._firmware, "front_guard": self._guard,
                    "pose_available": False, "range_available": False,
                    "collision": None, "last_action": self._last_action,
                    "action_count": self._action_count, "last_result": self._last_result,
                    "fault": str(self._failure) if self._failure else ("SESSION_STOPPED" if self._aborted else None)}

    def get_state(self, *, cancel_event=None, deadline=None):
        return self.snapshot()

    def get_pose(self, **_kwargs):
        raise RobotActionError("Ground pose is unavailable; no localization is installed")

    def get_distance(self, **_kwargs):
        raise RobotActionError("ToF is shown in WebUI telemetry, but is not exposed as a CaP observation by this motion backend")

    def close(self):
        """Best-effort T on exit; disconnect does not prove the motors stopped."""
        if self._closing.is_set():
            return
        with self._condition:
            self._aborted = True
            if self._port is not None and self._port.is_open:
                try:
                    self._request_stop()
                except RobotCommunicationError:
                    pass
            self._closing.set()
            self._condition.notify_all()
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(0.5)
        if self._port is not None:
            self._port.close()

    def __enter__(self):
        return self.connect()

    def __exit__(self, *_args):
        self.close()


def build_esp32_registry(backend):
    """Publish the tested hardware subset to validator, proxy and runtime."""
    registry = CapabilityRegistry()

    def check_budget(limits):
        if backend.snapshot()["action_count"] >= limits.max_actions:
            raise ActionLimitExceeded("Hardware action exceeds runtime budget; stop remains available")

    def validate_move(values, limits):
        move_mm(values["distance"])
        if values["distance"] > limits.max_move_distance:
            raise RobotActionError("Distance exceeds runtime limit")
        check_budget(limits)
        return values

    def validate_turn(values, limits):
        turn_tenths(values["angle"])
        if abs(values["angle"]) > limits.max_turn_angle:
            raise RobotActionError("Angle exceeds runtime limit")
        check_budget(limits)
        return values

    entries = (
        (CapabilitySpec("motion.move", "esp32-v1.8", (("move",),), (ParameterSpec("distance"),),
                        blocking=True, timeout_seconds=9,
                        description="supervised forward move distance=0.05..0.30 m in 0.001 m steps; fixed speed; waits for completion and stopped READY; obstacle guard OFF"), backend.move, validate_move),
        (CapabilitySpec("motion.turn", "esp32-v1.8", (("turn",),), (ParameterSpec("angle"),),
                        blocking=True, timeout_seconds=6,
                        description="relative turn angle=15..90 (left) or -90..-15 (right) deg in 0.1 degree steps; fixed speed; waits for valid result and stopped READY"), backend.turn, validate_turn),
        (CapabilitySpec("motion.stop", "esp32-v1.8", (("stop",),), timeout_seconds=2.5,
                        description="send T immediately; wait for drive-off acknowledgement and subsequent wheel-stopped READY"), backend.stop, None),
        (CapabilitySpec("state.get_state", "esp32-v1.8", (("get_state",),), observation=True,
                        return_fields={name: None for name in ("connected", "ready", "stopped", "front_guard", "pose_available", "range_available", "last_action", "action_count", "fault")},
                        description="serial session and feedback status; stopped may be unknown; no ground pose or collision sensing; ToF telemetry is displayed separately in WebUI"), backend.get_state, None),
    )
    for spec, handler, validator in entries:
        registry.register(spec, handler, validator, backend.emergency_stop)
    return registry
