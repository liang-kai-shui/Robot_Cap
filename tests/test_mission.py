"""Task grounding, persistent sessions, request bounds and private scoring."""
import json
from dataclasses import replace
import pytest
from agent.mission import MissionRunner
from agent.task_planner import GoalPlan
from navigation.planner import NavigationGoal
from navigation.runner import NavigationRunner
from providers.base import LLMResponse
from runtime.errors import PolicyGenerationError, PolicyGenerationTimeoutError
from task.mission_benchmark_tasks import MISSION_SCENARIOS
from task.waypoint_evaluator import evaluate_waypoints
from test_navigation import InlineTestExecutor


class ScriptProvider:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.contexts = []

    def generate_policy(self, instruction, robot_api, world_state, system_prompt=None):
        self.contexts.append(json.loads(world_state))
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        return LLMResponse(value, "test-model", 10, 3, None, 0.0)


@pytest.mark.parametrize("text", [
    '{}', '{"targets":[]}', '{"targets":["invisible"]}', '{"targets":[true]}',
    '{"targets":["A"],"code":"robot.move(10)"}', '{"targets":["A"],"targets":["A"]}',
    'not json', json.dumps({"targets": ["A"] * 9}),
])
def test_plan_rejects_unknown_targets_and_unbounded_or_executable_content(text):
    with pytest.raises(PolicyGenerationError):
        GoalPlan.parse(text, {"A": NavigationGoal(3, 1)})


def test_repeated_visits_are_preserved():
    assert GoalPlan.parse('{"targets":["A","B","A"]}', {"A": None, "B": None}).targets == ("A", "B", "A")


def test_model_abort_is_a_safe_failure_not_mission_completion():
    item = MISSION_SCENARIOS[0]
    result = MissionRunner().run(item.task, item.public_targets,
        provider=ScriptProvider(['{"stop_reason":"目标位置缺少足够信息"}']), executor=InlineTestExecutor())
    assert result.error_type == "MissionStopped" and not result.task_success
    assert result.metrics["actions"] == 0 and result.final_state["stopped"]


def test_multi_goal_session_preserves_map_runtime_and_cumulative_budget():
    item = MISSION_SCENARIOS[4]
    result = NavigationRunner().run_goals(item.task, [item.public_targets[name] for name in item.expected_order],
                                        executor=InlineTestExecutor())
    assert result.navigation_success and result.task_success and result.completed_goals == 3
    assert result.metrics["actions"] < 30
    starts = [event["command_id"] for event in result.trace if event["phase"] == "started"]
    assert starts == sorted(set(starts))
    assert max(step.get("map_revision", 0) for step in result.steps) == result.observed_map["revision"]
    bounded = NavigationRunner(max_actions=3).run_goals(item.task,
        [item.public_targets[name] for name in item.expected_order], executor=InlineTestExecutor())
    assert bounded.completed_goals == 1 and bounded.metrics["actions"] == 3
    assert bounded.error_type == "NavigationActionBudgetExceeded" and bounded.final_state["stopped"]


def test_hybrid_uses_one_plan_and_no_hidden_map_or_evaluator_data():
    item = MISSION_SCENARIOS[4]
    provider = ScriptProvider(['{"targets":["A","B","start"]}'])
    result = MissionRunner().run(item.task, item.public_targets, provider=provider, executor=InlineTestExecutor())
    assert result.task_success and result.completed_targets == ["A", "B", "start"]
    assert result.metrics["llm_calls"] == 1 and result.metrics["action_calls"] == 0
    assert result.model_requests[0]["input_tokens"] == 10
    assert "obstacles" not in str(provider.contexts) and "expected_order" not in str(provider.contexts)
    assert evaluate_waypoints(result.trace, result.initial_state,
        [item.public_targets[name] for name in item.expected_order])["stopped_order_success"]


def test_wrong_grounded_plan_can_reach_final_goal_but_fails_private_order_scoring():
    item = MISSION_SCENARIOS[4]
    provider = ScriptProvider(['{"targets":["start"]}'])
    result = MissionRunner().run(item.task, item.public_targets, provider=provider, executor=InlineTestExecutor())
    assert result.task_success and result.metrics["actions"] == 0
    score = evaluate_waypoints(result.trace, result.initial_state,
                               [item.public_targets[name] for name in item.expected_order])
    assert not score["stopped_order_success"]


@pytest.mark.parametrize("reply,error", [('{"targets":["secret"]}', "PolicyGenerationError"),
    (PolicyGenerationTimeoutError("transport timeout"), "PolicyGenerationTimeoutError")])
def test_bad_plan_or_transport_timeout_stops_before_motion_and_records_request(reply, error):
    item = MISSION_SCENARIOS[0]
    result = MissionRunner().run(item.task, item.public_targets, provider=ScriptProvider([reply]),
                                executor=InlineTestExecutor())
    assert result.error_type == error and result.metrics["actions"] == 0
    assert result.final_state["stopped"] and result.metrics["llm_calls"] == 1
    assert result.model_requests[0]["error_type"] == error


def test_direct_actions_share_the_worker_validation_guard_and_usage_accounting():
    item = MISSION_SCENARIOS[0]
    provider = ScriptProvider(['{"targets":["target"]}', 'robot.move(1.5)', 'robot.move(1.5)'])
    result = MissionRunner().run(item.task, item.public_targets, strategy="direct", provider=provider)
    assert result.task_success and result.metrics["actions"] == 2
    assert result.metrics["llm_calls"] == 3 and result.metrics["input_tokens"] == 30
    assert provider.contexts[1]["current"]["current_goal"]["x"] == 4


def test_unsafe_model_action_is_rejected_before_execution():
    item = MISSION_SCENARIOS[0]
    result = MissionRunner().run(item.task, item.public_targets, strategy="direct",
        provider=ScriptProvider(['{"targets":["target"]}', 'robot.move(1); robot.turn(90)']),
        executor=InlineTestExecutor())
    assert result.error_type == "UnsafePolicyError" and result.metrics["actions"] == 0
    assert result.metrics["llm_calls"] == 2 and result.final_state["stopped"]


def test_action_request_budget_is_independent_and_does_not_reset_on_replanning():
    item = MISSION_SCENARIOS[0]
    provider = ScriptProvider(['{"targets":["target"]}', *['robot.turn(90)'] * 3])
    result = MissionRunner(max_action_requests=3).run(item.task, item.public_targets, strategy="direct",
        provider=provider, executor=InlineTestExecutor())
    assert result.error_type == "ModelRequestBudgetExceeded"
    assert result.metrics["action_calls"] == 3 and result.metrics["actions"] == 3
    assert result.final_state["stopped"]


def test_waypoint_passage_and_stop_are_different_and_order_is_geometric():
    state = lambda x: {"pose": {"x": x, "y": 1, "heading": 0}, "stopped": True}
    trace = [{"kind": "action", "phase": "started", "command_id": 1,
              "capability_id": "motion.move", "state": state(1)},
             {"kind": "action", "phase": "completed", "command_id": 1,
              "capability_id": "motion.move", "state": state(4)}]
    goals = [NavigationGoal(2, 1), NavigationGoal(3, 1)]
    score = evaluate_waypoints(trace, state(1), goals)
    assert score["passage_success"] and not score["stopped_order_success"]
    reverse = evaluate_waypoints(trace, state(1), goals[::-1])
    assert reverse["waypoints_passed"] == 1


def test_replanning_is_bounded_and_never_claims_global_unreachability():
    item = MISSION_SCENARIOS[0]
    class RotatingProvider:
        def generate_policy(self, task, api, context, system_prompt=None):
            text = '{"targets":["target"]}' if "public_targets" in json.loads(context) else 'robot.turn(90)'
            return LLMResponse(text, "rotate-test", None, None, None, 0)
    result = MissionRunner(max_plan_requests=2).run(item.task, item.public_targets, strategy="direct",
        provider=RotatingProvider(), executor=InlineTestExecutor())
    assert result.error_type == "ModelRequestBudgetExceeded" and result.metrics["plan_calls"] == 2
    assert result.metrics["actions"] <= 80 and result.final_state["stopped"]
    assert result.plans[1]["trigger"] == "NavigationStalled"


def test_all_reference_missions_satisfy_private_ordered_stops():
    for item in MISSION_SCENARIOS:
        result = MissionRunner().run(item.task, item.public_targets, strategy="reference",
            reference_order=item.expected_order, executor=InlineTestExecutor())
        assert result.task_success, (item.task.id, result.error_type)
        assert evaluate_waypoints(result.trace, result.initial_state,
            [item.public_targets[name] for name in item.expected_order])["stopped_order_success"]
