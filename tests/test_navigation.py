"""Observed-map contracts, bounded exploration, and real worker integration."""
import ast
import time
from dataclasses import replace
import pytest
from agent.observations import sample_local_observation
from navigation.grid import ObservedGrid
from navigation.observation import RangeObservation
from navigation.planner import NavigationGoal, LocalNavigator, astar
from navigation.runner import NavigationRunner
from robot.backends.virtual import VirtualBackend
from robot.capabilities import CapabilityRegistry, build_default_registry
from robot.runtime import RobotRuntime
from robot.state import Pose
from runtime.decision_validator import validate_decision_policy
from runtime.executor import WorkerOutcome
from runtime.validator import robot_path
from task.interactive_benchmark_tasks import INTERACTIVE_TASKS
from task.navigation_benchmark_tasks import NAVIGATION_SCENARIOS


class InlineTestExecutor:
    """Fast algorithm tests; the CLI and the worker integration test use real spawn."""
    def execute(self, policy, world, initial, *, runtime):
        validate_decision_policy(policy, runtime.registry)
        call = ast.parse(policy).body[0].value
        identity = runtime.registry.resolve_public_path(robot_path(call.func)).spec.canonical_id
        reply = runtime.dispatch({"command_id": runtime.next_command_id, "capability_id": identity,
                                  "args": [ast.literal_eval(value) for value in call.args],
                                  "kwargs": {value.arg: ast.literal_eval(value.value) for value in call.keywords}})
        return WorkerOutcome(reply["ok"], reply.get("error_type"), reply.get("error_message"),
                             runtime.snapshot(), [], 0.0)


def test_range_limit_is_not_an_obstacle_and_single_beam_does_not_clear_area():
    grid = ObservedGrid()
    reading = RangeObservation.from_reading(3, Pose(0, 0, 0), "test.range")
    grid.update(reading)
    assert reading.distance_m == 2 and not reading.hit
    assert grid.state((7, 0)) == "free"
    assert grid.state((8, 0)) == "unknown" and grid.state((4, 1)) == "unknown"
    grid.update(RangeObservation.from_reading(2, Pose(0, 0, 0), "test.range"))
    assert grid.state((8, 0)) == "occupied"


@pytest.mark.parametrize("reading", [True, -1, None, float("nan"), float("inf")])
def test_invalid_range_never_updates_map(reading):
    grid = ObservedGrid()
    observation = RangeObservation.from_reading(reading, Pose(1, 1, 0), "test.range")
    assert not observation.valid
    with pytest.raises(ValueError):
        grid.update(observation)
    assert not grid.free and not grid.occupied


def test_stale_and_raw_physical_range_need_explicit_handling():
    reading = RangeObservation.from_reading(1, Pose(1, 1, 0), "test.range")
    with pytest.raises(ValueError, match="stale"):
        ObservedGrid().update(replace(reading, timestamp=time.time() - 10))
    with pytest.raises(ValueError, match="footprint adapter"):
        replace(reading, space="raw")


def test_astar_cannot_cross_unknown_or_occupied_space_or_cut_diagonally():
    grid = ObservedGrid()
    grid.free.update({(0, 0), (1, 0), (1, 1), (0, 1)})
    assert astar(grid, (0, 0), (1, 1)) in ([(0, 0), (0, 1), (1, 1)], [(0, 0), (1, 0), (1, 1)])
    grid.free.remove((1, 0))
    grid.free.remove((0, 1))
    assert astar(grid, (0, 0), (1, 1)) is None
    assert astar(grid, (0, 0), (2, 0)) is None


def test_equal_visible_observations_produce_equal_actions_despite_hidden_world_difference():
    decisions = []
    for task in (INTERACTIVE_TASKS[0], INTERACTIVE_TASKS[3]):
        runtime = RobotRuntime(VirtualBackend(task.world, task.initial))
        observation = RangeObservation.from_dict(sample_local_observation(runtime)["range_observation"])
        assert not observation.hit and observation.distance_m == 2
        decisions.append(LocalNavigator(NavigationGoal(4, 1), (1, 1)).decide(observation))
    assert decisions[0] == decisions[1]


@pytest.mark.parametrize("scenario", NAVIGATION_SCENARIOS[:4], ids=lambda item: item.task.id)
def test_frontier_commitment_avoids_bouncing_and_solves_paired_worlds(scenario):
    result = NavigationRunner().run(scenario.task, scenario.goal, executor=InlineTestExecutor())
    assert result.navigation_success and result.task_success
    assert not result.final_state["collision"] and result.final_state["stopped"]
    assert result.metrics["actions"] <= 30
    assert result.metrics["llm_calls"] == 0
    assert "obstacles" not in str([step["observation"] for step in result.steps])


def test_public_goal_is_independent_of_hidden_success_criteria_and_map_resets_between_runs():
    task = INTERACTIVE_TASKS[0]
    runner = NavigationRunner()
    result = runner.run(replace(task, success={"type": "unverified"}), NavigationGoal(4, 1),
                        executor=InlineTestExecutor())
    assert result.navigation_success and not result.task_success
    second = runner.run(task, NavigationGoal(4, 1), executor=InlineTestExecutor())
    assert second.task_success and second.metrics["actions"] == result.metrics["actions"]
    assert second.observed_map == result.observed_map


def test_unreachable_goal_ends_safely_with_a_budget_or_information_status():
    item = NAVIGATION_SCENARIOS[-1]
    result = NavigationRunner(max_actions=16).run(item.task, item.goal, executor=InlineTestExecutor())
    assert not result.task_success and not result.navigation_success
    assert result.error_type in ("NavigationActionBudgetExceeded", "NavigationInformationExhausted")
    assert result.final_state["stopped"] and not result.final_state["collision"]
    assert result.metrics["actions"] <= 16
    assert result.trace[-1]["capability_id"] == "system.emergency_stop"


def test_observation_budget_and_invalid_sensor_stop_before_unsafe_movement():
    item = NAVIGATION_SCENARIOS[1]
    result = NavigationRunner(max_observations=1).run(item.task, item.goal, executor=InlineTestExecutor())
    assert result.error_type == "NavigationObservationBudgetExceeded" and result.final_state["stopped"]
    backend = VirtualBackend(item.task.world, item.task.initial)
    registry = build_default_registry(backend)
    registry.get("sensors.get_distance").handler = lambda **kwargs: float("nan")
    result = NavigationRunner().run(item.task, item.goal, backend=backend, registry=registry,
                                    executor=InlineTestExecutor())
    assert result.error_type == "ValueError" and result.metrics["actions"] == 0
    assert result.final_state["stopped"]


def test_real_worker_resolves_nonlegacy_aliases_and_uses_monotonic_command_ids():
    task = INTERACTIVE_TASKS[0]
    backend = VirtualBackend(task.world, task.initial)
    default = build_default_registry(backend)
    registry = CapabilityRegistry()
    for spec in default.list_visible():
        item = default.get(spec.canonical_id)
        registry.register(replace(spec, public_paths=(("device", spec.canonical_id.rsplit(".", 1)[-1]),)),
                          item.handler, item.validator, item.emergency_stop)
    result = NavigationRunner().run(task, NavigationGoal(4, 1), backend=backend, registry=registry)
    assert result.task_success
    assert all(step["policy"].startswith("robot.device.") for step in result.steps)
    started = [event["command_id"] for event in result.trace if event["phase"] == "started"]
    assert started == sorted(set(started))
    assert "motion.move@v1" in result.used_capabilities
    assert "sensors.get_distance@v1" in result.used_capabilities


def test_missing_capability_is_rejected_without_motion():
    task = INTERACTIVE_TASKS[0]
    backend = VirtualBackend(task.world, task.initial)
    registry = build_default_registry(backend)
    registry.unregister("sensors.get_distance")
    result = NavigationRunner().run(task, NavigationGoal(4, 1), backend=backend, registry=registry)
    assert result.error_type == "ValueError" and result.metrics["actions"] == 0
    assert result.final_state["stopped"]


def test_exact_off_grid_goal_and_initially_at_goal_are_handled():
    item = NAVIGATION_SCENARIOS[6]
    result = NavigationRunner().run(item.task, item.goal, executor=InlineTestExecutor())
    assert result.task_success
    assert item.goal.reached(Pose(**result.final_state["pose"]))
    task = INTERACTIVE_TASKS[0]
    result = NavigationRunner().run(task, NavigationGoal(1, 1), executor=InlineTestExecutor())
    assert result.navigation_success and result.metrics["actions"] == 1
    assert result.steps[0]["policy"] == "robot.stop()"


def test_seeded_scene_sampling_is_repeatable_and_goals_are_separate_public_inputs():
    from task.navigation_benchmark_tasks import generate_navigation_scenarios
    first = generate_navigation_scenarios(3, 413)
    second = generate_navigation_scenarios(3, 413)
    assert first == second
    assert all(item.expected_reachable and item.category == "seeded-static" for item in first)
