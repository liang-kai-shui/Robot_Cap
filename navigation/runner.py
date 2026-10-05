"""Trusted navigation session using the existing validated worker execution chain."""
import math
import time
from dataclasses import asdict, dataclass, replace
from agent.observations import sample_local_observation
from navigation.observation import RangeObservation
from navigation.planner import LocalNavigator, NavigationAction, NavigationGoal
from robot.backends.virtual import VirtualBackend
from robot.navigation_safety import guard_navigation_registry
from robot.runtime import RobotRuntime
from runtime.decision_validator import validate_decision_policy
from runtime.executor import PolicyExecutor
from runtime.limits import RuntimeLimits
from task.evaluator import TaskEvaluator


class NavigationAdapter:
    """Resolve the implemented navigation primitives via registered public aliases."""
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

    def to_dict(self):
        return asdict(self)


class NavigationRunner:
    """P1/P2 deterministic baseline. Goal is explicit; hidden success rules stay in evaluation."""
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

    def run(self, task, goal: NavigationGoal, *, backend=None, registry=None,
            observe=sample_local_observation, executor=None):
        started = time.perf_counter()
        runtime = RobotRuntime(backend or VirtualBackend(task.world, task.initial, self.limits),
                               self.limits, registry)
        initial = runtime.snapshot()
        planner = LocalNavigator(goal, (initial.pose.x, initial.pose.y), self.resolution,
                                 self.margin, self.max_step, self.max_observation_age)
        executor = executor or PolicyExecutor(self.limits)
        steps = []
        error_type = error_message = None
        observed = 0
        planning_ms = execution_ms = 0.0
        safety_checks = []
        stagnant = 0
        try:
            runtime.registry, guard = guard_navigation_registry(
                runtime.registry, margin=self.margin, max_step=self.max_step)
            adapter = NavigationAdapter(runtime.registry)
            while True:
                state = runtime.snapshot()
                if state.collision:
                    error_type, error_message = "CollisionError", "Navigation state already records a collision"
                    break
                if goal.reached(state.pose) and state.stopped:
                    break
                if state.action_count >= self.limits.max_actions:
                    error_type, error_message = "NavigationActionBudgetExceeded", "Navigation action budget exhausted"
                    break
                if observed >= self.max_observations:
                    error_type, error_message = "NavigationObservationBudgetExceeded", "Navigation observation budget exhausted"
                    break
                trace_start = len(runtime.trace)
                observation = observe(runtime)
                observed += 1
                measurement = RangeObservation.from_dict(observation["range_observation"])
                measurement.require_fresh(self.max_observation_age)
                # The measured heading and position must describe the currently stopped robot.
                current = runtime.snapshot().pose
                if (math.hypot(current.x - measurement.pose.x, current.y - measurement.pose.y) > 1e-6
                        or abs((current.heading - measurement.pose.heading + 180) % 360 - 180) > 1e-6):
                    raise ValueError("Observation pose does not match current robot pose")
                revision_before = planner.grid.revision
                planning_start = time.perf_counter()
                if goal.reached(current):
                    action = NavigationAction("stop", 0, "goal", (goal.x, goal.y))
                else:
                    action = planner.decide(measurement)
                planning_ms += (time.perf_counter() - planning_start) * 1000
                if action is None:
                    error_type = "NavigationInformationExhausted"
                    error_message = "No reachable unobserved frontier; global unreachability is not established"
                    steps.append({"observation": observation, "policy": "", "purpose": "observe",
                                  "error_type": error_type, "error_message": error_message,
                                  "trace": list(runtime.trace[trace_start:])})
                    break
                policy = adapter.policy(action)
                validate_decision_policy(policy, runtime.registry)
                check_start = len(guard.checks)
                runtime.begin_decision(1)
                try:
                    outcome = executor.execute(policy, task.world, task.initial, runtime=runtime)
                finally:
                    runtime.end_decision()
                execution_ms += outcome.execution_ms
                checks = list(guard.checks[check_start:])
                safety_checks.extend(checks)
                steps.append({"observation": observation, "policy": policy, "purpose": action.purpose,
                              "target": action.target, "success": outcome.success,
                              "final_state": asdict(outcome.final_state), "error_type": outcome.error_type,
                              "error_message": outcome.error_message,
                              "map_revision": planner.grid.revision, "safety_checks": checks,
                              "trace": list(runtime.trace[trace_start:])})
                if not outcome.success:
                    error_type, error_message = outcome.error_type, outcome.error_message
                    runtime.emergency_stop()
                    if error_type != "MotionSafetyError":
                        break
                    # A new sensor sample drives the next plan; a rejected action is never replayed.
                else:
                    error_type = error_message = None
                moved = math.hypot(outcome.final_state.pose.x - current.x,
                                   outcome.final_state.pose.y - current.y) > 1e-6
                stagnant = 0 if moved or planner.grid.revision != revision_before else stagnant + 1
                if stagnant >= 12:
                    error_type, error_message = "NavigationStalled", "No movement or new map information for 12 steps"
                    break
        except Exception as exc:
            error_type, error_message = type(exc).__name__, str(exc)
        final = runtime.snapshot()
        navigation_success = goal.reached(final.pose) and final.stopped and not final.collision and error_type is None
        if not navigation_success:
            runtime.emergency_stop()
            final = runtime.snapshot()
        task_success = navigation_success and TaskEvaluator().evaluate(task, final)
        completed = [event for event in runtime.trace if event["kind"] == "action"
                     and event["phase"] == "completed"]
        distance = sum(abs(event["request"]["distance"]) for event in completed
                       if event["capability_id"] == "motion.move")
        rotation = sum(abs(event["request"]["angle"]) for event in completed
                       if event["capability_id"] == "motion.turn")
        used = list(dict.fromkeys(f"{event['capability_id']}@{event['capability_version']}"
                                 for event in runtime.trace if event["phase"] == "started"
                                 and event.get("capability_version")))
        metrics = {"total_ms": (time.perf_counter() - started) * 1000,
                   "planning_ms": planning_ms, "execution_ms": execution_ms, "llm_calls": 0,
                   "actions": final.action_count - initial.action_count,
                   "action_budget": self.limits.max_actions, "observations": observed,
                   "observation_budget": self.max_observations,
                   "scan_turns": sum(step["purpose"] == "scan" for step in steps),
                   "distance_m": distance, "rotation_degrees": rotation,
                   "nominal_motion_seconds": distance / self.limits.max_linear_speed
                                               + rotation / self.limits.max_angular_speed,
                   "guard_rejections": sum(not check["accepted"] for check in safety_checks),
                   "replans": planner.replans, "revisited_cells": sum(max(0, count - 1) for count in planner.grid.visits.values()),
                   "free_cells": len(planner.grid.free), "occupied_cells": len(planner.grid.occupied)}
        return NavigationResult(task.id, asdict(goal), navigation_success, task_success,
                                error_type, error_message, asdict(initial), asdict(final), steps,
                                list(runtime.trace), planner.grid.to_dict(),
                                runtime.registry.api_surface_signature(), used, metrics)
