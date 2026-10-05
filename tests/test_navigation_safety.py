"""Trusted pre-action range checks and registry-independent decision syntax."""
import threading
import time
import pytest
from robot.backends.virtual import VirtualBackend
from robot.capabilities import CapabilityRegistry, CapabilitySpec, ParameterSpec, build_default_registry
from robot.navigation_safety import ForwardRangeGuard, guard_navigation_registry
from robot.runtime import RobotRuntime
from robot.state import Pose
from runtime.decision_validator import validate_decision_policy
from runtime.errors import MotionSafetyError, UnsafePolicyError
from world.world import VirtualWorld


@pytest.mark.parametrize("reading", [None, True, float("nan"), float("inf"), -.1])
def test_invalid_sensor_never_reaches_motion(reading):
    moves = []
    guard = ForwardRangeGuard(lambda **kwargs: moves.append(kwargs), lambda **kwargs: reading)
    with pytest.raises(MotionSafetyError):
        guard(distance=.1, cancel_event=threading.Event(), deadline=time.monotonic() + 1)
    assert not moves and not guard.checks[0]["accepted"]


def test_guard_requires_sensor_and_rejects_unobserved_reverse():
    guard = ForwardRangeGuard(lambda **kwargs: pytest.fail("motion reached"), None)
    for distance in (.1, -.1):
        with pytest.raises(MotionSafetyError):
            guard(distance=distance, cancel_event=threading.Event(), deadline=time.monotonic() + 1)


def test_fresh_sensor_rejects_change_since_planning_and_preserves_caller_registry():
    backend = VirtualBackend(VirtualWorld(), Pose(1, 1, 0))
    registry = build_default_registry(backend)
    original = registry.get("motion.move").handler
    reading = [2.0]
    registry.get("sensors.get_distance").handler = lambda **kwargs: reading[0]
    guarded, guard = guard_navigation_registry(registry)
    runtime = RobotRuntime(backend, registry=guarded)
    assert runtime.dispatch({"command_id": 1, "capability_id": "sensors.get_distance"})["result"] == 2
    reading[0] = .25  # The scene changes during model generation.
    result = runtime.dispatch({"command_id": 2, "capability_id": "motion.move", "args": [.5]})
    assert result["error_type"] == "MotionSafetyError"
    assert backend.snapshot().action_count == 0 and backend.snapshot().pose.x == 1
    assert guard.checks[0]["allowed_distance_m"] == pytest.approx(.1)
    assert registry.get("motion.move").handler == original
    assert guarded.api_surface_signature() == registry.api_surface_signature()
    assert runtime.trace[-1]["error"]["type"] == "MotionSafetyError"


def test_sensor_and_motion_share_cancellation_and_deadline():
    calls = []
    def handler(**kwargs):
        calls.append(kwargs)
        return 2
    guard = ForwardRangeGuard(handler, handler)
    event, deadline = threading.Event(), time.monotonic() + 1
    guard(distance=1, speed=.2, cancel_event=event, deadline=deadline)
    assert all(call["cancel_event"] is event and call["deadline"] == deadline for call in calls)
    assert calls[1]["speed"] == .2 and guard.checks[0]["accepted"]


def test_slow_guard_sensor_is_bounded_by_action_timeout_and_cancelled():
    from runtime.limits import RuntimeLimits
    backend = VirtualBackend(VirtualWorld(), Pose(1, 1, 0))
    limits = RuntimeLimits(default_action_timeout_seconds=.03)
    registry = build_default_registry(backend)
    cancelled = threading.Event()
    def slow_sensor(*, cancel_event, deadline):
        if cancel_event.wait(.5):
            cancelled.set()
        return 2
    registry.get("sensors.get_distance").handler = slow_sensor
    guarded, _ = guard_navigation_registry(registry)
    runtime = RobotRuntime(backend, limits, guarded)
    started = time.monotonic()
    result = runtime.dispatch({"command_id": 1, "capability_id": "motion.move", "args": [.5]})
    assert result["error_type"] == "RobotActionTimeoutError"
    assert time.monotonic() - started < .3 and cancelled.is_set()
    assert backend.snapshot().action_count == 0 and backend.snapshot().stopped
    assert runtime.trace[-1]["capability_id"] == "system.emergency_stop"


def test_decision_validator_accepts_registered_unknown_action_and_rejects_removed_path():
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec("widget.foo.bar", "v1", (("widget", "foo", "bar"),),
                                     (ParameterSpec("value"),)), lambda **kwargs: None)
    validate_decision_policy("robot.widget.foo.bar(value=-3)", registry)
    guarded, guard = guard_navigation_registry(registry)
    assert guard is None and guarded.manifest() == registry.manifest()
    registry.unregister("widget.foo.bar")
    with pytest.raises(UnsafePolicyError):
        validate_decision_policy("robot.widget.foo.bar(3)", registry)
