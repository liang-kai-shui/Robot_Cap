"""Opt-in faults, honest truth scoring and bounded sensor recovery."""
import threading
import time
from dataclasses import replace
import pytest
from agent.mission import MissionRunner
from agent.observations import sample_local_observation
from navigation.grid import ObservedGrid
from navigation.observation import RangeObservation
from navigation.runner import NavigationRunner
from robot.backends.perturbed import PerturbationProfile, PerturbedVirtualBackend
from robot.navigation_safety import ForwardRangeGuard, guard_navigation_registry
from robot.runtime import RobotRuntime
from robot.state import Pose
from robustness_benchmark import run_scenario, summarize
from runtime.limits import RuntimeLimits
from runtime.errors import MotionSafetyError
from task.mission_benchmark_tasks import MISSION_SCENARIOS
from test_navigation import InlineTestExecutor


def backend_for(profile=PerturbationProfile(), limits=None, seed=10):
    task = MISSION_SCENARIOS[0].task
    return PerturbedVirtualBackend(task.world, task.initial, limits, profile=profile, seed=seed)


def move(runtime, distance=.5):
    return runtime.dispatch({"command_id": runtime.next_command_id, "capability_id": "motion.move",
                             "args": [distance], "kwargs": {}})


def test_range_error_never_clears_cells_beyond_conservative_interval():
    sample = RangeObservation(Pose(0, 0, 0), 1.1, 2, True, True, time.time(), "sensors.get_distance",
                              uncertainty_m=.2)
    grid = ObservedGrid()
    grid.update(sample)
    assert sample.conservative_distance_m == pytest.approx(.9)
    assert (3, 0) in grid.free and (4, 0) not in grid.free
    assert all(grid.point(cell)[0] <= .75 + 1e-8 for cell in grid.free)
    with pytest.raises(ValueError):
        replace(sample, uncertainty_m=-.01)


def test_guard_applies_uncertainty_and_actuator_bound_without_truth_access():
    backend = backend_for(PerturbationProfile(range_error_m=.03, move_error_fraction=.08))
    runtime = RobotRuntime(backend)
    runtime.registry, guard = guard_navigation_registry(runtime.registry,
        observation_adapter=backend.adapt_observation, distance_scale_bound=1.08)
    original_truth = backend.truth_snapshot
    # Ordinary sensor/guard code must not call the evaluator-only accessor.
    backend.truth_snapshot = lambda: (_ for _ in ()).throw(AssertionError("Truth leaked into guard"))
    response = move(runtime, 2)
    backend.truth_snapshot = original_truth
    assert response["error_type"] == "MotionSafetyError"
    assert guard.checks[-1]["allowed_distance_m"] == 1.5
    assert guard.checks[-1]["conservative_distance_m"] == pytest.approx(1.97)
    assert backend.snapshot().action_count == 0

    calls = []
    sample = RangeObservation(Pose(0, 0, 0), 1.1, 2, True, True, time.time(),
                              "sensors.get_distance", uncertainty_m=.2)
    guard = ForwardRangeGuard(lambda **kwargs: calls.append(kwargs), lambda **kwargs: 1.1,
        observation_adapter=lambda reading: sample, distance_scale_bound=1.08)
    with pytest.raises(MotionSafetyError, match="exceeds fresh guarded range"):
        guard(distance=.8, cancel_event=None, deadline=None)
    assert not calls and guard.checks[0]["allowed_distance_m"] == pytest.approx(.75 / 1.08)


@pytest.mark.parametrize("profile", [PerturbationProfile(dropout_probability=1),
                                     PerturbationProfile(sample_age_seconds=2.5)])
def test_persistent_fault_stops_without_moving_or_marking_free(profile):
    row, payload = run_scenario(MissionRunner(), MISSION_SCENARIOS[0], profile, 5,
                                executor=InlineTestExecutor())
    assert row["safe_abort"] and row["actions"] == 0 and not row["false_completion"]
    assert row["sensor_samples"] == 3 and row["sensor_retries"] == 2
    assert not payload["reported_episode"]["observed_map"]["free_cells"]
    assert len(payload["reported_episode"]["steps"][0]["observation"]["rejected_samples"]) == 3


class OneReadFault(PerturbedVirtualBackend):
    fault_on = 1

    def get_distance(self, **kwargs):
        value = super().get_distance(**kwargs)
        if self.sensor_samples == self.fault_on:
            self.last_sample = replace(self.last_sample, valid=False)
            return None
        return value


def test_transient_dropout_is_retried_at_rest_before_any_policy_executes():
    item = MISSION_SCENARIOS[0]
    backend = OneReadFault(item.task.world, item.task.initial)
    result = MissionRunner().run(item.task, item.public_targets, strategy="reference",
        reference_order=item.expected_order, backend=backend, observation_adapter=backend.adapt_observation,
        max_sensor_retries=2, executor=InlineTestExecutor())
    assert result.navigation_success and result.metrics["sensor_retries"] == 1
    assert result.metrics["observations"] == 3 and result.metrics["actions"] == 2
    assert result.steps[0]["observation"]["rejected_samples"][0]["range_observation"]["valid"] is False


@pytest.mark.parametrize("stale", [False, True])
def test_guard_rereads_sensor_and_rejects_fault_after_good_planner_observation(stale):
    class GuardFault(OneReadFault):
        fault_on = 2

        def get_distance(self, **kwargs):
            value = super().get_distance(**kwargs)
            if stale and self.sensor_samples == 2:
                self.last_sample = replace(self.last_sample, valid=True, timestamp=time.time() - 3)
                return 2.0
            return value

    item = MISSION_SCENARIOS[0]
    backend = GuardFault(item.task.world, item.task.initial)
    session = NavigationRunner().session(item.task, item.public_targets["target"], backend=backend,
        observation_adapter=backend.adapt_observation, executor=InlineTestExecutor())
    session.initialize()
    session.runtime.emergency_stop()
    assert session.advance(item.public_targets["target"]) == "running"
    assert session.steps[0]["error_type"] == "MotionSafetyError"
    assert backend.snapshot().action_count == 0
    assert backend.truth_snapshot().pose == item.task.initial
    assert not session.guard.checks[0]["accepted"]


def test_faults_are_seeded_and_pose_feedback_is_separate_from_private_truth():
    profile = PerturbationProfile(move_error_fraction=.08, odometry_scale_error=.03, initial_x_error_m=.2)
    backends = [backend_for(profile, seed=123) for _ in range(2)]
    for backend in backends:
        backend.move(.5)
        backend.turn(90)
        backend.move(.3)
    assert backends[0].snapshot() == backends[1].snapshot()
    assert [e["request"] for e in backends[0].truth_events] == [e["request"] for e in backends[1].truth_events]
    runtime = RobotRuntime(backends[0])
    visible = sample_local_observation(runtime)
    assert visible["pose_estimate"] != vars(backends[0].truth_snapshot().pose)
    assert not any(key in str(visible) for key in ("truth", "obstacles", "fault_seed", "odometry_scale_error"))
    assert runtime.registry.get("system.truth_snapshot") is None


def test_sensor_fault_configuration_does_not_shift_actuator_random_stream():
    profiles = [PerturbationProfile(move_error_fraction=.08),
                PerturbationProfile(move_error_fraction=.08, range_error_m=.03, dropout_probability=.5)]
    values = []
    for profile in profiles:
        backend = backend_for(profile, seed=50)
        backend.emergency_stop()
        for _ in range(10):
            backend.get_distance()
        backend.move(.5)
        starts = [event for event in backend.truth_events if event["phase"] == "started"]
        completed = [event for event in backend.truth_events if event["capability_id"] == "motion.move"
                     and event["phase"] == "completed"]
        assert starts[0]["command_id"] == completed[0]["command_id"]
        values.append(completed[0]["request"]["actual"])
    assert values[0] == values[1]


def test_noisy_hit_lower_bound_stays_below_true_geometric_range():
    item = MISSION_SCENARIOS[1]
    backend = PerturbedVirtualBackend(item.task.world, item.task.initial,
        profile=PerturbationProfile(range_error_m=.03), seed=5)
    truth_range = item.task.world.distance_ahead(item.task.initial)
    for _ in range(30):
        backend.get_distance()
        assert backend.last_sample.hit
        assert backend.last_sample.conservative_distance_m <= truth_range + 1e-9


def test_independent_grader_detects_completion_with_wrong_initial_localization():
    row, payload = run_scenario(MissionRunner(), MISSION_SCENARIOS[0],
        PerturbationProfile(initial_x_error_m=.2), 5, executor=InlineTestExecutor())
    assert row["reported_completion"] and row["false_completion"] and not row["actual_mission_success"]
    assert row["pose_error_m"] == pytest.approx(.2)
    assert payload["reported_episode"]["task_success"]
    assert not row["actual_final_goal_success"]


def test_bounded_move_error_is_corrected_from_feedback():
    row, _ = run_scenario(MissionRunner(), MISSION_SCENARIOS[4],
        PerturbationProfile(move_error_fraction=.08), 1004, executor=InlineTestExecutor())
    assert row["actual_mission_success"] and not row["collision_attempt"]
    assert row["pose_error_m"] == 0 and row["actions"] <= 80


def test_action_deadline_cancels_delay_without_late_motion():
    limits = replace(RuntimeLimits(), default_action_timeout_seconds=.03)
    backend = backend_for(PerturbationProfile(action_delay_seconds=.5), limits)
    runtime = RobotRuntime(backend, limits)
    started = time.monotonic()
    response = move(runtime)
    assert time.monotonic() - started < .3
    assert response["error_type"] == "RobotActionTimeoutError"
    time.sleep(.05)
    assert backend.truth_snapshot().pose == MISSION_SCENARIOS[0].task.initial
    assert backend.snapshot().stopped and backend.cancellations == 1
    assert any(event["kind"] == "safety" for event in backend.truth_events)


def test_emergency_stop_interrupts_wait_without_waiting_for_action_lock():
    backend = backend_for(PerturbationProfile(action_delay_seconds=.5))
    runtime = RobotRuntime(backend)
    replies = []
    thread = threading.Thread(target=lambda: replies.append(move(runtime)))
    thread.start()
    until = time.monotonic() + .5
    while not backend.truth_events and time.monotonic() < until:
        time.sleep(.001)
    backend.emergency_stop()
    thread.join(.2)
    assert not thread.is_alive() and replies[0]["error_type"] == "RobotEmergencyStopError"
    assert backend.snapshot().stopped and backend.snapshot().action_count == 0


def test_retry_budget_does_not_reset_observation_budget():
    row, _ = run_scenario(MissionRunner(max_observations=2), MISSION_SCENARIOS[0],
        PerturbationProfile(dropout_probability=1), 1, executor=InlineTestExecutor())
    assert row["error_type"] == "NavigationObservationBudgetExceeded"
    assert row["observations"] == 2 and row["safe_abort"]


@pytest.mark.parametrize("kwargs", [{"dropout_probability": 1.1}, {"range_error_m": -1},
    {"move_error_fraction": 1}, {"odometry_scale_error": .8}, {"action_delay_seconds": float("nan")},
    {"initial_x_error_m": True}])
def test_invalid_fault_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        PerturbationProfile(**kwargs)


def test_summary_separates_safe_abort_false_completion_and_success():
    rows = []
    for profile in (PerturbationProfile(), PerturbationProfile(dropout_probability=1),
                    PerturbationProfile(initial_x_error_m=.2)):
        row, _ = run_scenario(MissionRunner(), MISSION_SCENARIOS[0], profile, 5,
                              executor=InlineTestExecutor())
        rows.append({**row, "profile": "test"})
    summary = summarize(rows)["test"]
    assert summary["actual_mission_success"] == summary["safe_abort"] == summary["false_completion"] == 1
    assert summary["model_calls"] == 0


def test_real_worker_can_run_perturbed_backend_without_access_to_truth():
    row, payload = run_scenario(MissionRunner(), MISSION_SCENARIOS[0],
        PerturbationProfile(move_error_fraction=.08, range_error_m=.03), 11)
    assert row["actual_mission_success"] and row["llm_calls"] == 0
    assert payload["private_truth"]["trace"]
    assert all("truth" not in step["policy"] for step in payload["reported_episode"]["steps"])
