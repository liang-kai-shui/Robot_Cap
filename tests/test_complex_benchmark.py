"""Reference policies prove scenario solvability; grading detects shortcuts."""
from copy import deepcopy
from dataclasses import replace
import pytest
from agent.runner import run_task
from complex_benchmark import ReferenceProvider
from robot.state import Pose
from runtime.errors import CollisionError, WorldBoundaryError
from task.complex_benchmark_tasks import COMPLEX_SCENARIOS
from task.complex_evaluator import evaluate_complex
from world.obstacle import Obstacle
from world.world import VirtualWorld


def test_clearance_changes_collision_and_range_without_changing_legacy_default():
    legacy = VirtualWorld(obstacles=(Obstacle(2, 2, 1, 1),))
    narrow = VirtualWorld(obstacles=(Obstacle(2, 2, 1, 1),), clearance=.2)
    legacy.check_motion(Pose(1, 1.9, 0), Pose(4, 1.9, 0))
    with pytest.raises(CollisionError):
        narrow.check_motion(Pose(1, 1.9, 0), Pose(4, 1.9, 0))
    assert legacy.distance_ahead(Pose(1, 2.5, 0)) == 1
    assert narrow.distance_ahead(Pose(1, 2.5, 0)) == .8
    with pytest.raises(WorldBoundaryError):
        narrow.check_pose(Pose(.1, 1, 0))


@pytest.mark.parametrize("scenario", COMPLEX_SCENARIOS, ids=lambda item: item.task.id)
def test_every_complex_scenario_has_a_valid_reference_solution(scenario):
    result = run_task(scenario.task, ReferenceProvider(), run_dir=None)
    assert result.execution_success, (scenario.task.id, result.error_type, result.error_message)
    assert result.task_success, scenario.task.id
    assert evaluate_complex(scenario, result) == (True, [])


def test_complex_grading_rejects_endpoint_only_and_missing_observations():
    navigation = COMPLEX_SCENARIOS[2]
    result = run_task(navigation.task, ReferenceProvider(), run_dir=None)
    shortened = [event for event in result.trace if not (
        event.get("capability_id") == "motion.move" and event.get("phase") == "completed"
        and (event.get("state") or {}).get("pose", {}).get("y") == 4.5
    )]
    ok, failures = evaluate_complex(navigation, replace(result, trace=shortened))
    assert not ok and "waypoint_order" in failures

    sensing = COMPLEX_SCENARIOS[6]
    result = run_task(sensing.task, ReferenceProvider(), run_dir=None)
    no_reads = [event for event in result.trace if event.get("capability_id") != "sensors.get_distance"]
    ok, failures = evaluate_complex(sensing, replace(result, trace=no_reads))
    assert not ok and "distance_reads" in failures


def test_complex_grading_checks_speed_and_distance_budgets():
    scenario = COMPLEX_SCENARIOS[9]
    result = run_task(scenario.task, ReferenceProvider(), run_dir=None)
    trace = deepcopy(result.trace)
    first_move = next(event for event in trace if event.get("capability_id") == "motion.move"
                      and event.get("phase") == "completed")
    first_move["request"]["speed"] = .5
    ok, failures = evaluate_complex(scenario, replace(result, trace=trace))
    assert not ok and "speed_schedule" in failures

    trace = deepcopy(result.trace)
    first_move = next(event for event in trace if event.get("capability_id") == "motion.move"
                      and event.get("phase") == "completed")
    first_move["request"]["distance"] = 2
    ok, failures = evaluate_complex(scenario, replace(result, trace=trace))
    assert not ok and "distance_budget" in failures


def test_feedback_grading_requires_observation_between_steps():
    scenario = COMPLEX_SCENARIOS[4]
    result = run_task(scenario.task, ReferenceProvider(), run_dir=None)
    trace = deepcopy(result.trace)
    completed_reads = [index for index, event in enumerate(trace) if
                       event.get("capability_id") == "sensors.get_distance" and event.get("phase") == "completed"]
    trace.pop(completed_reads[2])
    ok, failures = evaluate_complex(scenario, replace(result, trace=trace))
    assert not ok and "read_before_each_move" in failures

    trace = deepcopy(result.trace)
    first_move = next(event for event in trace if event.get("capability_id") == "motion.move"
                      and event.get("phase") == "completed")
    first_move["request"]["distance"] = 1
    ok, failures = evaluate_complex(scenario, replace(result, trace=trace))
    assert not ok and "step_distance" in failures
