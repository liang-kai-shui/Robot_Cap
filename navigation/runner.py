"""Persistent trusted navigation session using validated single-action workers."""
import math
import time
from dataclasses import asdict, dataclass, field, replace
from uuid import uuid4
from agent.observations import sample_local_observation
from navigation.observation import RangeObservation
from navigation.planner import LocalNavigator, NavigationAction
from robot.backends.virtual import VirtualBackend
from robot.capabilities import API_VERSION
from robot.navigation_safety import guard_navigation_registry
from robot.runtime import RobotRuntime
from runtime.decision_validator import validate_decision_policy
from runtime.executor import PolicyExecutor
from runtime.limits import RuntimeLimits
from task.evaluator import TaskEvaluator


class NavigationAdapter:
    """Resolve implemented navigation primitives through registered public aliases."""
    IDENTITIES = {"move": "motion.move", "turn": "motion.turn", "stop": "motion.stop"}

    def __init__(self, registry):
        self.registry = registry
        for identity in (*self.IDENTITIES.values(), "sensors.get_distance"):
            if registry.get(identity) is None:
                raise ValueError(f"Navigation requires registered capability {identity}")

    def policy(self, action):
        spec = self.registry.get(self.IDENTITIES[action.operation]).spec
        path = "robot." + ".".join(spec.public_paths[0])
        if action.operation == "stop":
            return path + "()"
        name = "distance" if action.operation == "move" else "angle"
        spec.bind((), {name: action.value})
        return f"{path}({name}={action.value!r})"


@dataclass
class NavigationResult:
    task_id: str
    goal: dict
    navigation_success: bool
    task_success: bool
    error_type: str | None
    error_message: str | None
    initial_state: dict
    final_state: dict
    steps: list[dict]
    trace: list[dict]
    observed_map: dict
    api_surface_signature: list[str]
    used_capabilities: list[str]
    metrics: dict
    goals: list[dict] = field(default_factory=list)
    completed_goals: int = 0
    plans: list[dict] = field(default_factory=list)
    model_requests: list[dict] = field(default_factory=list)
    completed_targets: list[str] = field(default_factory=list)
    episode_id: str = field(default_factory=lambda: uuid4().hex)
    instruction: str = ""
    api_version: str = API_VERSION
    execution_success: bool = False

    def to_dict(self):
        return asdict(self)


class NavigationSessionError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class NavigationSession:
    """One runtime, one observed map and cumulative budgets across all subgoals."""
    def __init__(self, task, goal, limits, max_observations, resolution, margin,
                 max_step, max_observation_age, *, backend=None, registry=None,
                 observe=sample_local_observation, executor=None, observation_adapter=None,
                 heading_tolerance_deg=1e-6, distance_scale_bound=1.0, max_sensor_retries=0):
        if isinstance(max_sensor_retries, bool) or not isinstance(max_sensor_retries, int) or max_sensor_retries < 0:
            raise ValueError("Sensor retries must be a nonnegative integer")
        self.started = time.perf_counter()
        self.task = task
        self.runtime = RobotRuntime(backend or VirtualBackend(task.world, task.initial, limits), limits, registry)
        self.initial = self.runtime.snapshot()
        self.planner = LocalNavigator(goal, (self.initial.pose.x, self.initial.pose.y),
                                      resolution, margin, max_step, max_observation_age,
                                      heading_tolerance_deg, distance_scale_bound)
        self.executor = executor or PolicyExecutor(limits)
        self.observe_fn = observe
        self.max_observations = max_observations
        self.max_observation_age = max_observation_age
        self.margin, self.max_step = margin, max_step
        self.observation_adapter, self.distance_scale_bound = observation_adapter, distance_scale_bound
        self.max_sensor_retries, self.sensor_retries = max_sensor_retries, 0
        self.observed = self.stagnant = 0
        self.steps, self.safety_checks = [], []
        self.planning_ms = self.execution_ms = 0.0
        self.error_type = self.error_message = None

    def initialize(self):
        self.runtime.registry, self.guard = guard_navigation_registry(
            self.runtime.registry, margin=self.margin, max_step=self.max_step,
            observation_adapter=self.observation_adapter, max_observation_age=self.max_observation_age,
            distance_scale_bound=self.distance_scale_bound)
        self.adapter = NavigationAdapter(self.runtime.registry)

    def reached(self, goal):
        state = self.runtime.snapshot()
        return goal.reached(state.pose) and state.stopped and not state.collision

    def public_context(self):
        state = self.runtime.snapshot()
        return {"pose_estimate": asdict(state.pose), "stopped": state.stopped,
                "remaining_actions": self.runtime.limits.max_actions - state.action_count,
                "remaining_observations": self.max_observations - self.observed,
                "observed_map_summary": {"free_cells": len(self.planner.grid.free),
                                         "occupied_cells": len(self.planner.grid.occupied)}}

    def fail(self, error_type, message):
        self.error_type, self.error_message = error_type, message
        self.runtime.emergency_stop()

    def advance(self, goal, policy_factory=None):
        """Observe and execute at most one action. Returns reached/running/failed."""
        state = self.runtime.snapshot()
        if self.reached(goal):
            return "reached"
        trace_start, check_start = len(self.runtime.trace), len(self.guard.checks)
        observation, policy, purpose, target = {}, "", "observe", (goal.x, goal.y)
        revision_before = self.planner.grid.revision
        try:
            if state.collision:
                raise NavigationSessionError("CollisionError", "Navigation state already records a collision")
            if state.action_count >= self.runtime.limits.max_actions:
                raise NavigationSessionError("NavigationActionBudgetExceeded", "Navigation action budget exhausted")
            if self.observed >= self.max_observations:
                raise NavigationSessionError("NavigationObservationBudgetExceeded", "Navigation observation budget exhausted")
            attempts = []
            for attempt in range(self.max_sensor_retries + 1):
                if self.observed >= self.max_observations:
                    raise NavigationSessionError("NavigationObservationBudgetExceeded", "Navigation observation budget exhausted")
                observation = self.observe_fn(self.runtime)
                self.observed += 1
                if self.observation_adapter is not None:
                    reading = observation["range_observation"]["distance_m"]
                    measurement = self.observation_adapter(reading)
                    observation["range_observation"] = measurement.to_dict()
                    observation["front_distance_m"] = measurement.distance_m
                measurement = RangeObservation.from_dict(observation["range_observation"])
                observation["rejected_samples"] = attempts
                try:
                    measurement.require_fresh(self.max_observation_age)
                    break
                except ValueError as exc:
                    # Retry only while stopped, without invoking a policy or
                    # using invalid data to modify the map. Every read is budgeted.
                    attempts.append({"range_observation": measurement.to_dict(), "reason": str(exc)})
                    if attempt == self.max_sensor_retries or not self.runtime.snapshot().stopped:
                        raise
                    self.sensor_retries += 1
            current = self.runtime.snapshot().pose
            if (math.hypot(current.x - measurement.pose.x, current.y - measurement.pose.y) > 1e-6
                    or abs((current.heading - measurement.pose.heading + 180) % 360 - 180) > 1e-6):
                raise ValueError("Observation pose does not match current robot pose")
            self.planner.set_goal(goal)
            self.planner.grid.update(measurement, self.max_observation_age)
            observation["navigation_limits"] = {
                "max_actions_per_decision": 1, "max_forward_step_m": self.max_step,
                "range_margin_m": self.margin,
                "safe_forward_distance_m": round(max(0, min(self.max_step,
                    (measurement.conservative_distance_m - self.margin) / self.distance_scale_bound)), 9)}
            observation.update(self.public_context())
            observation["recent_steps"] = [
                {"policy": step["policy"], "success": step["success"],
                 "pose_after": step.get("final_state", {}).get("pose"), "error_type": step["error_type"]}
                for step in self.steps[-4:]]
            if policy_factory is None:
                start = time.perf_counter()
                action = (NavigationAction("stop", 0, "goal", target) if goal.reached(current)
                          else self.planner.decide(measurement))
                self.planning_ms += (time.perf_counter() - start) * 1000
                if action is None:
                    raise NavigationSessionError("NavigationInformationExhausted",
                        "No reachable unobserved frontier; global unreachability is not established")
                policy, purpose, target = self.adapter.policy(action), action.purpose, action.target
            else:
                policy, purpose = policy_factory(observation), "model-action"
            validate_decision_policy(policy, self.runtime.registry)
            self.runtime.begin_decision(1)
            try:
                outcome = self.executor.execute(policy, self.task.world, self.task.initial, runtime=self.runtime)
            finally:
                self.runtime.end_decision()
            self.execution_ms += outcome.execution_ms
            self.error_type, self.error_message = outcome.error_type, outcome.error_message
            if not outcome.success:
                self.runtime.emergency_stop()
            checks = list(self.guard.checks[check_start:])
            self.safety_checks.extend(checks)
            self.steps.append({"observation": observation, "policy": policy, "purpose": purpose,
                "target": target, "success": outcome.success, "final_state": asdict(self.runtime.snapshot()),
                "error_type": self.error_type, "error_message": self.error_message,
                "map_revision": self.planner.grid.revision, "safety_checks": checks,
                "trace": list(self.runtime.trace[trace_start:])})
            if not outcome.success and self.error_type != "MotionSafetyError":
                return "failed"
            moved = math.hypot(outcome.final_state.pose.x - current.x, outcome.final_state.pose.y - current.y) > 1e-6
            self.stagnant = 0 if moved or self.planner.grid.revision != revision_before else self.stagnant + 1
            if self.stagnant >= 12:
                self.fail("NavigationStalled", "No movement or new map information for 12 steps")
                self.steps[-1]["session_status"] = self.error_type
                self.steps[-1]["trace"] = list(self.runtime.trace[trace_start:])
                return "failed"
            return "reached" if outcome.success and self.reached(goal) else "running"
        except Exception as exc:
            self.fail(exc.code if isinstance(exc, NavigationSessionError) else type(exc).__name__, str(exc))
            self.steps.append({"observation": observation, "policy": policy, "purpose": purpose,
                "target": target, "success": False, "error_type": self.error_type,
                "error_message": self.error_message, "safety_checks": [],
                "trace": list(self.runtime.trace[trace_start:])})
            return "failed"

    def result(self, goals, completed_goals):
        final = self.runtime.snapshot()
        success = bool(goals) and completed_goals == len(goals) and self.error_type is None and self.reached(goals[-1])
        if not success:
            self.runtime.emergency_stop()
            final = self.runtime.snapshot()
        completed = [event for event in self.runtime.trace if event["kind"] == "action" and event["phase"] == "completed"]
        distance = sum(abs(event["request"]["distance"]) for event in completed if event["capability_id"] == "motion.move")
        rotation = sum(abs(event["request"]["angle"]) for event in completed if event["capability_id"] == "motion.turn")
        used = list(dict.fromkeys(f"{event['capability_id']}@{event['capability_version']}"
            for event in self.runtime.trace if event["phase"] == "started" and event.get("capability_version")))
        metrics = {"total_ms": (time.perf_counter() - self.started) * 1000,
            "planning_ms": self.planning_ms, "execution_ms": self.execution_ms, "llm_calls": 0,
            "actions": final.action_count - self.initial.action_count, "action_budget": self.runtime.limits.max_actions,
            "observations": self.observed, "observation_budget": self.max_observations,
            "sensor_retries": self.sensor_retries,
            "scan_turns": sum(step["purpose"] == "scan" for step in self.steps),
            "distance_m": distance, "rotation_degrees": rotation,
            "nominal_motion_seconds": distance / self.runtime.limits.max_linear_speed + rotation / self.runtime.limits.max_angular_speed,
            "guard_rejections": sum(not check["accepted"] for check in self.safety_checks),
            "replans": self.planner.replans, "revisited_cells": sum(max(0, n - 1) for n in self.planner.grid.visits.values()),
            "free_cells": len(self.planner.grid.free), "occupied_cells": len(self.planner.grid.occupied)}
        result = NavigationResult(self.task.id, asdict(goals[-1]) if goals else {}, success,
            success and TaskEvaluator().evaluate(self.task, final), self.error_type, self.error_message,
            asdict(self.initial), asdict(final), self.steps, list(self.runtime.trace), self.planner.grid.to_dict(),
            self.runtime.registry.api_surface_signature(), used, metrics, [asdict(goal) for goal in goals], completed_goals)
        result.instruction = self.task.instruction
        result.execution_success = self.error_type is None
        return result


class NavigationRunner:
    def __init__(self, limits=None, max_actions=80, max_observations=160,
                 resolution=.25, margin=.15, max_step=1.5, max_observation_age=2.0):
        if (any(not isinstance(value, int) or isinstance(value, bool) or value < 1
                for value in (max_actions, max_observations))
                or not math.isfinite(max_observation_age) or max_observation_age <= 0):
            raise ValueError("Navigation budgets must be positive")
        base = limits or RuntimeLimits()
        self.limits = replace(base, max_actions=min(max_actions, base.max_actions))
        self.max_observations = max_observations
        self.resolution, self.margin = resolution, margin
        self.max_step = min(max_step, self.limits.max_move_distance)
        self.max_observation_age = max_observation_age

    def session(self, task, goal, **options):
        return NavigationSession(task, goal, self.limits, self.max_observations, self.resolution,
            self.margin, self.max_step, self.max_observation_age, **options)

    def run(self, task, goal, **options):
        return self.run_goals(task, [goal], **options)

    def run_goals(self, task, goals, **options):
        goals = list(goals)
        if not goals:
            raise ValueError("Navigation requires a nonempty public goal sequence")
        session = self.session(task, goals[0], **options)
        completed = 0
        try:
            session.initialize()
            for goal in goals:
                while True:
                    status = session.advance(goal)
                    if status == "reached":
                        completed += 1
                        break
                    if status == "failed":
                        return session.result(goals, completed)
        except Exception as exc:
            session.fail(type(exc).__name__, str(exc))
        return session.result(goals, completed)
