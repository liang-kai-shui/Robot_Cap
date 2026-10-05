"""Cross-turn state, local information boundary, and session safety."""
import json
import pytest
from agent.interactive import InteractiveRunner
from interactive_benchmark import ReferenceLocalProvider
from robot.backends.virtual import VirtualBackend
from robot.runtime import RobotRuntime
from robot.state import Pose
from runtime.executor import PolicyExecutor
from runtime.limits import RuntimeLimits
from task.interactive_benchmark_tasks import INTERACTIVE_TASKS
from world.world import VirtualWorld


class RecordingProvider(ReferenceLocalProvider):
    def __init__(self):
        self.contexts = []
        self.policies = []

    def generate_policy(self, task, robot_api, world_state, system_prompt=None):
        self.contexts.append(json.loads(world_state))
        response = super().generate_policy(task, robot_api, world_state, system_prompt)
        self.policies.append(response.text)
        return response


def test_persistent_runtime_executes_each_worker_command_once():
    world = VirtualWorld()
    initial = Pose(1, 1, 0)
    limits = RuntimeLimits()
    runtime = RobotRuntime(VirtualBackend(world, initial, limits), limits)
    executor = PolicyExecutor(limits)
    first = executor.execute("robot.move(0.5)", world, initial, runtime=runtime)
    second = executor.execute("robot.move(0.5)", world, initial, runtime=runtime)
    assert first.success and second.success
    assert second.final_state.pose.x == 2
    assert second.final_state.action_count == 2
    assert first.trace[0]["command_id"] == 1
    assert second.trace[0]["command_id"] == 2
    assert len(first.logs) == len(second.logs) == 1
    assert runtime.next_command_id == 3


def test_hidden_map_observation_changes_plan_without_leaking_world():
    open_provider = RecordingProvider()
    blocked_provider = RecordingProvider()
    open_result = InteractiveRunner().run(INTERACTIVE_TASKS[0], open_provider)
    blocked_result = InteractiveRunner().run(INTERACTIVE_TASKS[1], blocked_provider)
    assert open_result.task_success and blocked_result.task_success
    assert len(open_result.decisions) == 2
    assert len(blocked_result.decisions) > len(open_result.decisions)
    assert open_provider.policies[0] != blocked_provider.policies[0]
    assert open_provider.contexts[0]["current"]["front_distance_m"] > 1.2
    assert blocked_provider.contexts[0]["current"]["front_distance_m"] < 1.2
    assert open_provider.contexts[0]["current"]["front_distance_m"] == 2.0
    for context in (*open_provider.contexts, *blocked_provider.contexts):
        assert context["observation_only"] is True
        assert "obstacles" not in json.dumps(context)
        assert "world" not in json.dumps(context)
    action_ids = [event["command_id"] for event in blocked_result.trace
                  if event["phase"] == "started" and event["command_id"] is not None]
    assert len(action_ids) == len(set(action_ids))
    assert blocked_result.final_state.action_count == 7


class FixedProvider:
    def __init__(self, policy):
        self.policy = policy

    def generate_policy(self, task, robot_api, world_state, system_prompt=None):
        from providers.base import LLMResponse
        return LLMResponse(self.policy, "fixed-test", None, None, None, 0.0)


def test_decision_limit_stops_nonprogressing_policy():
    result = InteractiveRunner(max_decisions=2).run(INTERACTIVE_TASKS[0], FixedProvider("robot.stop()"))
    assert not result.task_success
    assert result.error_type == "DecisionLimitExceeded"
    assert result.final_state.stopped
    assert len(result.decisions) == 2
    assert any(event["capability_id"] == "system.emergency_stop" for event in result.trace)


@pytest.mark.parametrize("policy", ["import os", "while True:\n    pass", "robot.get_distance()",
                                    "robot.move(0.1)\nrobot.turn(90)", "robot.move(0.1 + 0.1)"])
def test_interactive_contract_rejects_before_execution(policy):
    result = InteractiveRunner().run(INTERACTIVE_TASKS[0], FixedProvider(policy))
    assert not result.task_success and result.error_type == "UnsafePolicyError"
    assert result.final_state.stopped and result.final_state.action_count == 0
    assert result.error_message


def test_per_decision_action_budget_is_enforced_by_trusted_runtime():
    policy = "\n".join("robot.move(0.1)" for _ in range(4))
    task = INTERACTIVE_TASKS[0]
    runtime = RobotRuntime(VirtualBackend(task.world, task.initial))
    runtime.begin_decision(1)
    result = PolicyExecutor().execute(policy, task.world, task.initial, runtime=runtime)
    assert not result.success and result.error_type == "ActionLimitExceeded"
    assert result.final_state.pose.x == pytest.approx(1.1)
    assert result.final_state.stopped
    assert result.final_state.action_count == 1


def test_guard_rejection_becomes_feedback_and_task_can_continue():
    class RecoveringProvider(FixedProvider):
        def __init__(self):
            self.contexts = []

        def generate_policy(self, task, robot_api, world_state, system_prompt=None):
            context = json.loads(world_state)["current"]
            self.contexts.append(context)
            self.policy = "robot.move(2)" if len(self.contexts) == 1 else "robot.move(1.5)"
            return super().generate_policy(task, robot_api, world_state, system_prompt)

    provider = RecoveringProvider()
    result = InteractiveRunner().run(INTERACTIVE_TASKS[0], provider)
    assert result.task_success and result.error_type is None and result.error_message is None
    assert result.decisions[0].error_type == "MotionSafetyError"
    assert result.decisions[0].safety_checks[0]["accepted"] is False
    assert result.final_state.action_count == 2
    assert provider.contexts[1]["previous_step"]["error_message"]
    assert provider.contexts[1]["recent_steps"][0]["pose_before"] == provider.contexts[1]["recent_steps"][0]["pose_after"]


def test_generation_failure_preserves_message_elapsed_time_and_stop():
    from runtime.errors import PolicyGenerationTimeoutError
    class FailingProvider:
        def generate_policy(self, *args):
            raise PolicyGenerationTimeoutError("test socket timeout")

    result = InteractiveRunner().run(INTERACTIVE_TASKS[0], FailingProvider())
    assert result.error_type == "PolicyGenerationTimeoutError"
    assert result.error_message == "test socket timeout"
    assert result.llm_ms > 0 and result.decisions[0].llm_ms == result.llm_ms
    assert result.final_state.action_count == 0 and result.final_state.stopped


def test_recent_steps_are_bounded_and_turn_is_followed_by_new_reading():
    provider = RecordingProvider()
    result = InteractiveRunner().run(INTERACTIVE_TASKS[1], provider)
    assert result.task_success
    first, second = [context["current"] for context in provider.contexts[:2]]
    assert provider.policies[0] == "robot.turn(90)"
    assert second["pose_estimate"]["heading"] == 90
    assert first["front_distance_m"] < second["front_distance_m"]
    assert len(provider.contexts[-1]["current"]["recent_steps"]) == 4
