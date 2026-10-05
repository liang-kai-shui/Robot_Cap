"""Trusted parent-process robot command dispatcher."""
from collections import OrderedDict
from dataclasses import asdict, is_dataclass
import math
import time
from robot.backends.base import RobotBackend
from robot.capabilities import CAPABILITIES
from robot.trace import TraceEvent
from runtime.errors import (RobotActionError, RobotActionTimeoutError, RobotBackendError,
                            MoveLimitExceeded, TurnLimitExceeded, MoveBelowResolutionError)
from runtime.limits import RuntimeLimits
from world.geometry import NUMERIC_RESOLUTION


class RobotRuntime:
    """Validate and execute robot commands in the trusted process."""

    def __init__(self, backend: RobotBackend, limits: RuntimeLimits | None = None):
        self.backend = backend
        self.limits = limits or RuntimeLimits()
        self.trace: list[dict] = []
        self.recent_command_results: OrderedDict[int, dict] = OrderedDict()

    def snapshot(self):
        """Read backend state without recording a policy observation."""
        return self.backend.snapshot()

    def _state(self) -> dict | None:
        try:
            return asdict(self.snapshot())
        except Exception:
            return None

    def _event(self, kind, command_id, capability, action, phase, request=None, result=None, error=None):
        self.trace.append(TraceEvent(kind, command_id, capability, action, phase,
                                     request or {}, result, error, self._state()).to_dict())

    def _number(self, value, name, maximum, positive=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RobotActionError(f"{name} must be a number")
        try:
            value = float(value)
        except OverflowError as exc:
            raise RobotActionError(f"{name} is out of range") from exc
        if not math.isfinite(value) or (positive and value <= 0) or abs(value) > maximum:
            raise RobotActionError(f"{name} exceeds safe range")
        return value

    def _validate(self, name: str, args: tuple, kwargs: dict) -> dict:
        values = CAPABILITIES[name].bind(args, kwargs)
        if name == "move":
            distance = values["distance"]
            if isinstance(distance, bool) or not isinstance(distance, (int, float)):
                raise TypeError("Move distance must be a number")
            try:
                distance = float(distance)
            except OverflowError as exc:
                raise MoveLimitExceeded("Move distance is out of range") from exc
            if not math.isfinite(distance) or abs(distance) > self.limits.max_move_distance:
                raise MoveLimitExceeded(f"Move exceeds {self.limits.max_move_distance} m")
            if 0 < abs(distance) < NUMERIC_RESOLUTION:
                raise MoveBelowResolutionError(f"Move is below {NUMERIC_RESOLUTION:g} m resolution")
            values["distance"] = distance
            if "speed" in values and values["speed"] is not None:
                values["speed"] = self._number(values["speed"], "speed", self.limits.max_linear_speed, True)
        if name == "turn":
            angle = values["angle"]
            if isinstance(angle, bool) or not isinstance(angle, (int, float)):
                raise TypeError("Turn angle must be a number")
            try:
                angle = float(angle)
            except OverflowError as exc:
                raise TurnLimitExceeded("Turn angle is out of range") from exc
            if not math.isfinite(angle) or abs(angle) > self.limits.max_turn_angle:
                raise TurnLimitExceeded(f"Turn exceeds {self.limits.max_turn_angle} degrees")
            values["angle"] = angle
            if "speed" in values and values["speed"] is not None:
                values["speed"] = self._number(values["speed"], "speed", self.limits.max_angular_speed, True)
        return values

    def dispatch(self, request: dict) -> dict:
        """Execute a command once and cache its response by command ID."""
        command_id = request.get("command_id")
        if isinstance(command_id, bool) or not isinstance(command_id, int) or command_id < 1:
            return {"ok": False, "error_type": "RobotCommunicationError", "error_message": "Invalid command ID"}
        if command_id in self.recent_command_results:
            return self.recent_command_results[command_id]
        name = request.get("action")
        if name not in CAPABILITIES:
            response = {"ok": False, "error_type": "RobotActionError", "error_message": "Unknown capability"}
            self._cache(command_id, response)
            return response
        capability = CAPABILITIES[name]
        kind = "observation" if capability.observation else "action"
        args, kwargs = request.get("args", []), request.get("kwargs", {})
        raw = {"args": args, "kwargs": kwargs}
        self._event(kind, command_id, capability.namespace, name, "started", raw)
        try:
            if not isinstance(args, list) or not isinstance(kwargs, dict):
                raise RobotActionError("Invalid command arguments")
            try:
                values = self._validate(name, tuple(args), kwargs)
            except Exception as exc:
                if hasattr(self.backend, "record_rejection"):
                    self.backend.record_rejection(name, raw, type(exc).__name__)
                raise
            deadline = time.monotonic() + self.limits.default_action_timeout_seconds
            result = getattr(self.backend, name)(**values)
            if time.monotonic() > deadline:
                raise RobotActionTimeoutError(f"{name} exceeded action deadline")
            if is_dataclass(result):
                result = asdict(result)
            response = {"ok": True, "result": result}
            self._event(kind, command_id, capability.namespace, name, "completed", values, result)
        except Exception as exc:
            if not isinstance(exc, (RobotActionError, TypeError, ValueError)):
                # Preserve existing simulator exception types while distinguishing unknown backend failures.
                from runtime.errors import RobotError
                if not isinstance(exc, RobotError):
                    exc = RobotBackendError(str(exc))
            if isinstance(exc, RobotActionTimeoutError):
                self.emergency_stop()
            response = {"ok": False, "error_type": type(exc).__name__, "error_message": str(exc)}
            self._event(kind, command_id, capability.namespace, name, "failed", raw, error={"type": type(exc).__name__, "message": str(exc)})
        self._cache(command_id, response)
        return response

    def _cache(self, command_id: int, response: dict) -> None:
        self.recent_command_results[command_id] = response
        if len(self.recent_command_results) > 128:
            self.recent_command_results.popitem(last=False)

    def emergency_stop(self) -> None:
        """Force the trusted backend to stop regardless of policy state."""
        try:
            self.backend.emergency_stop()
            self._event("safety", None, "motion", "emergency_stop", "completed")
        except Exception as exc:
            self._event("safety", None, "motion", "emergency_stop", "failed",
                        error={"type": type(exc).__name__, "message": str(exc)})
