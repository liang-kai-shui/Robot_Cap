"""Generic registration and independently enforced timeout contracts."""
import time
import pytest
from agent.prompts import generate_robot_api_prompt, generate_system_prompt
from agent.runner import run_task
from robot.backends.mock import MockRobotBackend
from robot.capabilities import CapabilityRegistry, CapabilitySpec, ParameterSpec
from robot.proxy import RobotProxy
from robot.runtime import RobotRuntime
from robot.state import Pose
from runtime.errors import UnsafePolicyError
from runtime.executor import PolicyExecutor
from runtime.limits import RuntimeLimits
from runtime.validator import PolicyValidator
from providers.base import LLMProvider, LLMResponse
from task.task import Task
from world.world import VirtualWorld


def test_dynamic_capability_drives_prompt_validator_proxy_runtime_and_episode_surface():
    calls = []
    registry = CapabilityRegistry()
    spec = CapabilitySpec("widget.foo.bar", "v3", (("widget", "foo", "bar"),),
                          (ParameterSpec("code"),), return_fields={"ok": None},
                          observation=True, description="read widget code")

    def handler(code, *, cancel_event, deadline):
        calls.append(code)
        return {"ok": code == 7}

    registry.register(spec, handler)
    policy = "result = robot.widget.foo.bar(code=7)\nif result.ok:\n    pass"
    assert "robot.widget.foo.bar(code)" in generate_robot_api_prompt(registry)
    assert "robot.widget.foo.bar(...)" in generate_system_prompt(registry)
    PolicyValidator(registry).validate(policy)
    with pytest.raises(UnsafePolicyError):
        PolicyValidator(registry).validate("robot.widget.foo.magic(code=7)")
    with pytest.raises(UnsafePolicyError):
        PolicyValidator(registry).validate("robot.widget.foo.bar(banana=7)")
    with pytest.raises(UnsafePolicyError):
        PolicyValidator(registry).validate("x = robot.widget.foo.bar(code=7).hidden")

    outcome = PolicyExecutor().execute(policy, VirtualWorld(), Pose(1, 1, 0), registry=registry)
    assert outcome.success, outcome.error_message
    assert calls == [7]
    assert outcome.api_surface_signature == ["widget.foo.bar@v3"]
    assert outcome.trace[0]["capability_id"] == "widget.foo.bar"
    assert [event["phase"] for event in outcome.trace] == ["started", "completed"]

    proxy = RobotProxy(None, registry.manifest(), 0.1)
    with pytest.raises(AttributeError):
        proxy.widget.foo.magic
    registry.unregister("widget.foo.bar")
    assert "widget" not in generate_robot_api_prompt(registry)
    with pytest.raises(UnsafePolicyError):
        PolicyValidator(registry).validate(policy)
    with pytest.raises(AttributeError):
        RobotProxy(None, registry.manifest(), 0.1).widget


def test_run_task_uses_one_custom_registry_for_generation_validation_and_execution(tmp_path):
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec("widget.ping", "v1", (("widget", "ping"),),
                                     description="ping attached widget"),
                      lambda *, cancel_event, deadline: None)

    class CaptureProvider(LLMProvider):
        def generate_policy(self, task, robot_api, world_state, system_prompt=None):
            assert "robot.widget.ping()" in robot_api
            assert "robot.move" not in robot_api
            assert "robot.widget.ping(...)" in system_prompt
            assert "robot.move" not in system_prompt
            return LLMResponse("robot.widget.ping()", "capture", None, None, None, 0.0)

    task = Task("widget", "ping", Pose(1, 1, 0), {"type": "unverified"})
    result = run_task(task, CaptureProvider(), run_dir=tmp_path, registry=registry)
    assert result.execution_success
    assert result.api_surface_signature == ["widget.ping@v1"]
    assert result.trace[0]["capability_id"] == "widget.ping"
    import json
    episode = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))["episode"]
    assert episode["api_surface_signature"] == ["widget.ping@v1"]
    assert episode["used_capabilities"] == ["widget.ping@v1"]


def test_same_terminal_name_can_coexist_and_legacy_alias_collision_is_rejected():
    registry = CapabilityRegistry()
    noop = lambda *, cancel_event, deadline: None
    registry.register(CapabilitySpec("alpha.stop", "v1", (("alpha", "stop"),)), noop)
    registry.register(CapabilitySpec("beta.stop", "v1", (("beta", "stop"),)), noop)
    PolicyValidator(registry).validate("robot.alpha.stop()\nrobot.beta.stop()")
    assert RobotRuntime(registry=registry).dispatch({"command_id": 1, "capability_id": "alpha.stop", "args": [], "kwargs": {}})["ok"]
    assert RobotRuntime(registry=registry).dispatch({"command_id": 2, "capability_id": "beta.stop", "args": [], "kwargs": {}})["ok"]
    registry.register(CapabilitySpec("legacy.first", "v1", (("stop",),)), noop)
    with pytest.raises(ValueError, match="Ambiguous public path"):
        registry.register(CapabilitySpec("legacy.second", "v1", (("stop",),)), noop)


def test_long_valid_action_outlives_communication_and_policy_compute_budget():
    backend = MockRobotBackend(delay_seconds=0.2)
    limits = RuntimeLimits(policy_timeout_seconds=0.05, communication_timeout_seconds=0.05,
                           default_action_timeout_seconds=0.5)
    result = PolicyExecutor(limits).execute("robot.move(0.5)", VirtualWorld(), Pose(1, 1, 0), backend=backend)
    assert result.success, (result.error_type, result.error_message)
    assert backend.calls == 1 and backend.emergency_stops == 0
    assert result.execution_ms >= 200


def test_action_deadline_cancels_before_full_mock_duration():
    backend = MockRobotBackend(delay_seconds=1.0)
    limits = RuntimeLimits(policy_timeout_seconds=0.05, communication_timeout_seconds=0.05,
                           default_action_timeout_seconds=0.1)
    started = time.perf_counter()
    result = PolicyExecutor(limits).execute("robot.move(0.5)", VirtualWorld(), Pose(1, 1, 0), backend=backend)
    elapsed = time.perf_counter() - started
    assert result.error_type == "RobotActionTimeoutError"
    assert backend.emergency_stops == 1 and backend.cancellations == 1
    assert elapsed < 0.8
    action = [event for event in result.trace if event["kind"] == "action"]
    assert [event["phase"] for event in action] == ["started", "cancelled"]
    assert action[0]["capability_id"] == "motion.move"
    assert any(event["capability_id"] == "system.emergency_stop" for event in result.trace)


def test_registered_component_stop_is_called_without_backend_type_assumptions():
    registry = CapabilityRegistry()
    stopped = []
    cancelled = []

    def slow_handler(*, cancel_event, deadline):
        while not cancel_event.is_set():
            time.sleep(0.002)
        cancelled.append(True)

    registry.register(CapabilitySpec("odd_device.wait", "v1", (("odd_device", "wait"),),
                                     timeout_seconds=0.03), slow_handler,
                      emergency_stop=lambda: stopped.append(True))
    runtime = RobotRuntime(registry=registry)
    result = runtime.dispatch({"command_id": 1, "capability_id": "odd_device.wait", "args": [], "kwargs": {}})
    assert result["error_type"] == "RobotActionTimeoutError"
    assert stopped == [True] and cancelled == [True]
    assert [event["phase"] for event in runtime.trace] == ["started", "cancelled", "completed"]


def test_policy_runaway_still_times_out_and_stops():
    backend = MockRobotBackend()
    result = PolicyExecutor(RuntimeLimits(policy_timeout_seconds=0.15)).execute(
        "while True:\n    pass", VirtualWorld(), Pose(1, 1, 0), backend=backend)
    assert result.error_type == "PolicyTimeoutError"
    assert backend.emergency_stops == 1


def test_prompt_no_longer_requires_stop_after_finite_motion():
    prompt = generate_system_prompt().lower()
    api = generate_robot_api_prompt().lower()
    assert "call robot.stop() when the task is complete" not in prompt
    assert "blocking robot calls return only after completion" in prompt
    assert "finite blocking move" in api and "finite blocking turn" in api


def test_validated_hint_cannot_bypass_executor_trust_boundary():
    backend = MockRobotBackend()
    with pytest.raises(UnsafePolicyError):
        PolicyExecutor().execute("import os", VirtualWorld(), Pose(1, 1, 0),
                                 validated=True, backend=backend)
    assert backend.calls == 0
