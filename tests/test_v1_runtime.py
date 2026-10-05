import json
import inspect
import pytest
from robot.backends.mock import MockRobotBackend
from robot.capabilities import DEFAULT_REGISTRY
from robot.runtime import RobotRuntime
from robot.state import Pose
from robot.virtual import VirtualRobot
from runtime.executor import PolicyExecutor
from runtime.errors import PolicySyntaxError, UnsafePolicyError
from runtime.limits import RuntimeLimits
from runtime.validator import PolicyValidator
from world.world import VirtualWorld


def crash_worker(*_args):
    """Simulate an abrupt worker exit in a spawned child."""
    import os
    os._exit(3)


def test_worker_only_imports_proxy_and_policy_reaches_parent_backend():
    from runtime import worker
    source = inspect.getsource(worker)
    assert "VirtualRobot" not in source and "VirtualBackend" not in source
    backend = MockRobotBackend()
    outcome = PolicyExecutor().execute("robot.move(0.5)", VirtualWorld(), Pose(1, 1, 0), backend=backend)
    assert outcome.success and backend.calls == 1
    assert [e["phase"] for e in outcome.trace] == ["started", "completed"]


def test_worker_timeout_stops_parent_backend():
    backend = MockRobotBackend()
    limits = RuntimeLimits(timeout_seconds=0.5)
    outcome = PolicyExecutor(limits).execute("while True:\n    pass", VirtualWorld(), Pose(1, 1, 0), backend=backend)
    assert outcome.error_type == "PolicyTimeoutError"
    assert backend.emergency_stops == 1
    assert outcome.trace[-1]["kind"] == "safety"


def test_abnormal_worker_exit_stops_parent_backend(monkeypatch):
    import runtime.executor as executor_module
    monkeypatch.setattr(executor_module, "run_worker", crash_worker)
    backend = MockRobotBackend()
    outcome = PolicyExecutor().execute("robot.stop()", VirtualWorld(), Pose(1, 1, 0), backend=backend)
    assert not outcome.success and outcome.error_type == "PolicyExecutionError"
    assert backend.emergency_stops == 1


def test_finite_motion_stops_and_proxy_supports_speed_forms():
    policy = "robot.move(0.5)\nrobot.turn(angle=90, speed=45)\nrobot.move(distance=0.25, speed=0.2)\nrobot.move(0.25, 0.2)"
    outcome = PolicyExecutor().execute(policy, VirtualWorld(), Pose(1, 1, 0))
    assert outcome.success, outcome.error_message
    assert outcome.final_state.pose == Pose(1.5, 1.5, 90)
    assert outcome.final_state.stopped
    completed = [e for e in outcome.trace if e["phase"] == "completed"]
    assert completed[-1]["request"]["speed"] == 0.2
    assert completed[1]["request"]["speed"] == 45.0
    assert completed[-1]["request"]["speed"] == 0.2


@pytest.mark.parametrize("speed", [-1, 0, float("nan"), float("inf"), True, "fast", 0.6])
def test_unsafe_linear_speed_rejected(speed):
    backend = MockRobotBackend()
    runtime = RobotRuntime(backend)
    result = runtime.dispatch({"command_id": 1, "action": "move", "args": [0.5], "kwargs": {"speed": speed}})
    assert not result["ok"] and result["error_type"] == "RobotActionError"
    assert backend.calls == 0
    assert [e["phase"] for e in runtime.trace] == ["started", "failed"]


def test_angular_speed_limit_and_action_timeout():
    backend = MockRobotBackend(delay_seconds=0.02)
    runtime = RobotRuntime(backend, RuntimeLimits(default_action_timeout_seconds=0.001))
    response = runtime.dispatch({"command_id": 1, "action": "turn", "args": [90], "kwargs": {"speed": 45}})
    assert response["error_type"] == "RobotActionTimeoutError"
    assert backend.emergency_stops == 1
    assert [e["phase"] for e in runtime.trace if e["kind"] == "action"] == ["started", "cancelled"]
    assert RobotRuntime(MockRobotBackend()).dispatch({"command_id": 2, "action": "turn", "args": [90], "kwargs": {"speed": 91}})["error_type"] == "RobotActionError"


def test_duplicate_command_id_returns_cached_result():
    backend = MockRobotBackend()
    runtime = RobotRuntime(backend)
    request = {"command_id": 3, "action": "move", "args": [0.5], "kwargs": {}}
    first = runtime.dispatch(request)
    assert first == runtime.dispatch(request)
    assert backend.calls == 1 and len(runtime.trace) == 2


def test_parent_rejects_unsafe_distance_before_mock_backend():
    backend = MockRobotBackend()
    runtime = RobotRuntime(backend)
    response = runtime.dispatch({"command_id": 1, "action": "move", "args": [3], "kwargs": {}})
    assert response["error_type"] == "MoveLimitExceeded"
    assert backend.calls == 0


def test_observation_trace_and_legacy_config():
    from main import load_config
    _, _, limits = load_config("config/default.yaml")
    backend = MockRobotBackend()
    runtime = RobotRuntime(backend, limits)
    response = runtime.dispatch({"command_id": 1, "action": "get_state", "args": [], "kwargs": {}})
    assert response["ok"]
    assert [item["kind"] for item in runtime.trace] == ["observation", "observation"]
    assert limits.effective_policy_timeout_seconds == limits.timeout_seconds


def test_backend_error_trace_and_serialization():
    backend = MockRobotBackend(error=RuntimeError("motor fault"))
    runtime = RobotRuntime(backend)
    result = runtime.dispatch({"command_id": 1, "action": "move", "args": [0.5], "kwargs": {}})
    assert result["error_type"] == "RobotBackendError"
    assert [e["phase"] for e in runtime.trace] == ["started", "failed"]
    json.dumps(runtime.trace)


@pytest.mark.parametrize("policy", [
    "robot.move(foo=1)", "robot.turn(speed=1, banana=2)",
    "robot.move(0.5, distance=0.6)", "robot.move(0.5, 0.2, 0.1)",
    "robot.stop(speed=0.2)", "robot.move(distance=0.5, speed=0.1, speed=0.2)",
    "import os", "robot = 1", "x = 1\nx += 1", "def f(): pass",
])
def test_schema_validator_rejects_invalid_calls_and_syntax(policy):
    with pytest.raises((UnsafePolicyError, PolicySyntaxError)):
        PolicyValidator().validate(policy)


def test_direct_virtual_actions_are_finite():
    robot = VirtualRobot(VirtualWorld(), Pose(1, 1, 0))
    robot.move(0.5)
    assert robot.snapshot().stopped
    robot.turn(90)
    assert robot.snapshot().stopped
    assert {item.canonical_id.split(".")[0] for item in DEFAULT_REGISTRY.list_visible()} == {"motion", "sensors", "state"}
