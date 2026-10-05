"""Trusted generic capability dispatcher and cooperative action cancellation."""
from collections import OrderedDict
from dataclasses import asdict, is_dataclass
import threading
import time
from robot.capabilities import CapabilityRegistry, RegisteredCapability, build_default_registry
from robot.trace import TraceEvent
from runtime.errors import (RobotActionError, RobotActionTimeoutError, RobotBackendError,
                            RobotEmergencyStopError, RobotError, ActionLimitExceeded)
from runtime.limits import RuntimeLimits


class RobotRuntime:
    """Execute registered trusted handlers without knowing hardware namespaces."""

    def __init__(self, backend=None, limits: RuntimeLimits | None = None,
                 registry: CapabilityRegistry | None = None):
        self.backend = backend
        self.limits = limits or RuntimeLimits()
        self.registry = registry if registry is not None else build_default_registry(backend)
        self.trace: list[dict] = []
        self.recent_command_results: OrderedDict[int, dict] = OrderedDict()
        self.next_command_id = 1
        self._decision_actions_remaining: int | None = None
        self._poisoned = False

    def begin_decision(self, max_actions: int) -> None:
        """Bound action calls inside one generated policy, independently of mission limits."""
        if max_actions < 1:
            raise ValueError("Decision action budget must be positive")
        self._decision_actions_remaining = max_actions

    def end_decision(self) -> None:
        self._decision_actions_remaining = None

    def snapshot(self):
        """Read a backend state without recording a policy observation."""
        if self.backend is None or not hasattr(self.backend, "snapshot"):
            return None
        return self.backend.snapshot()

    def _state(self) -> dict | None:
        try:
            state = self.snapshot()
            return asdict(state) if is_dataclass(state) else state
        except Exception:
            return None

    def _event(self, kind, command_id, capability_id, version, phase,
               request=None, result=None, error=None):
        self.trace.append(TraceEvent(kind=kind, command_id=command_id,
                                     capability_id=capability_id, capability_version=version,
                                     phase=phase, request=request or {}, result=result,
                                     error=error, state=self._state()).to_dict())

    def _resolve(self, request: dict) -> RegisteredCapability | None:
        identity = request.get("capability_id")
        if isinstance(identity, str):
            return self.registry.get(identity)
        # Accept old direct callers, but resolve their alias through the registry.
        alias = request.get("action")
        if isinstance(alias, str):
            return self.registry.resolve_public_path((alias,))
        return None

    def accepts(self, request: dict) -> bool:
        """Check whether an IPC request names a registered capability."""
        return self._resolve(request) is not None

    def action_timeout(self, request: dict) -> float:
        """Return the registered action deadline for an accepted request."""
        item = self._resolve(request)
        if item is None:
            return self.limits.default_action_timeout_seconds
        return item.spec.timeout_seconds or self.limits.default_action_timeout_seconds

    def dispatch(self, request: dict) -> dict:
        """Validate and execute a command once, returning a cached duplicate result."""
        command_id = request.get("command_id")
        if isinstance(command_id, bool) or not isinstance(command_id, int) or command_id < 1:
            return {"ok": False, "error_type": "RobotCommunicationError", "error_message": "Invalid command ID"}
        self.next_command_id = max(self.next_command_id, command_id + 1)
        if command_id in self.recent_command_results:
            return self.recent_command_results[command_id]
        item = self._resolve(request)
        if item is None:
            response = {"ok": False, "error_type": "RobotActionError", "error_message": "Unknown capability"}
            self._cache(command_id, response)
            return response
        spec = item.spec
        kind = "observation" if spec.observation else "action"
        args, kwargs = request.get("args", []), request.get("kwargs", {})
        raw = {"args": args, "kwargs": kwargs}
        self._event(kind, command_id, spec.canonical_id, spec.version, "started", raw)
        validated_args = False
        try:
            if self._poisoned:
                raise RobotEmergencyStopError("Runtime has an uncancelled action")
            if not isinstance(args, list) or not isinstance(kwargs, dict):
                raise RobotActionError("Invalid command arguments")
            try:
                values = spec.bind(tuple(args), kwargs)
                if item.validator:
                    values = item.validator(values, self.limits)
                validated_args = True
            except Exception as exc:
                if self.backend is not None and hasattr(self.backend, "record_rejection"):
                    self.backend.record_rejection(spec.canonical_id, raw, type(exc).__name__)
                raise
            if not spec.observation and self._decision_actions_remaining is not None:
                if self._decision_actions_remaining == 0:
                    raise ActionLimitExceeded("Generated policy exceeded its per-decision action budget")
                self._decision_actions_remaining -= 1
            result = self._run_handler(item, values, command_id)
            if is_dataclass(result):
                result = asdict(result)
            response = {"ok": True, "result": result}
            self._event(kind, command_id, spec.canonical_id, spec.version, "completed", values, result)
        except Exception as exc:
            if not isinstance(exc, RobotError) and (validated_args or not isinstance(exc, (TypeError, ValueError))):
                exc = RobotBackendError(str(exc))
            phase = "cancelled" if isinstance(exc, RobotActionTimeoutError) else "failed"
            response = {"ok": False, "error_type": type(exc).__name__, "error_message": str(exc)}
            self._event(kind, command_id, spec.canonical_id, spec.version, phase, raw,
                        error={"type": type(exc).__name__, "message": str(exc)})
            if isinstance(exc, RobotActionTimeoutError):
                self.emergency_stop()
        self._cache(command_id, response)
        return response

    def _run_handler(self, item: RegisteredCapability, values: dict, command_id: int):
        timeout = item.spec.timeout_seconds or self.limits.default_action_timeout_seconds
        deadline = time.monotonic() + timeout
        cancel_event = threading.Event()
        outcome: dict = {}

        def invoke():
            try:
                outcome["result"] = item.handler(**values, cancel_event=cancel_event, deadline=deadline)
            except Exception as exc:
                outcome["error"] = exc

        thread = threading.Thread(target=invoke, name=f"robot-command-{command_id}", daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive() or time.monotonic() > deadline:
            cancel_event.set()
            thread.join(0.01)  # Cooperative cleanup only; Python cannot kill a running thread.
            if thread.is_alive():
                self._poisoned = True
            raise RobotActionTimeoutError(f"{item.spec.canonical_id} exceeded action deadline")
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("result")

    def _cache(self, command_id: int, response: dict) -> None:
        self.recent_command_results[command_id] = response
        if len(self.recent_command_results) > 128:
            self.recent_command_results.popitem(last=False)

    def emergency_stop(self) -> None:
        """Stop all registered components independently of action handlers."""
        callbacks = list(self.registry.emergency_stops())
        if self.backend is not None and hasattr(self.backend, "emergency_stop"):
            if self.backend.emergency_stop not in callbacks:
                callbacks.append(self.backend.emergency_stop)
        failures = []
        for stop in callbacks:
            try:
                stop()
            except Exception as exc:
                failures.append(exc)
        if failures:
            exc = failures[0]
            self._event("safety", None, "system.emergency_stop", None, "failed",
                        error={"type": type(exc).__name__, "message": str(exc)})
        else:
            self._event("safety", None, "system.emergency_stop", None, "completed")
