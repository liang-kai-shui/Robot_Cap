"""Software-only receding-horizon task session with a persistent trusted runtime."""
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Callable
from agent.coder import AgentCoder
from agent.observations import sample_local_observation
from robot.backends.virtual import VirtualBackend
from robot.capabilities import CapabilityRegistry
from robot.navigation_safety import guard_navigation_registry
from robot.runtime import RobotRuntime
from robot.state import RobotState
from runtime.executor import PolicyExecutor
from runtime.decision_validator import validate_decision_policy
from runtime.limits import RuntimeLimits
from task.evaluator import TaskEvaluator
from task.task import Task


@dataclass
class DecisionRecord:
    index: int
    observation: dict
    policy: str
    success: bool
    error_type: str | None
    llm_ms: float | None
    execution_ms: float
    actions_after: int
    trace: list[dict] = field(default_factory=list)
    error_message: str | None = None
    final_pose: dict = field(default_factory=dict)
    safety_checks: list[dict] = field(default_factory=list)
    llm_ttft_ms: float | None = None


@dataclass
class InteractiveResult:
    task_id: str
    task_success: bool
    error_type: str | None
    final_state: RobotState
    decisions: list[DecisionRecord]
    trace: list[dict]
    total_ms: float
    llm_ms: float
    input_tokens: int | None
    output_tokens: int | None
    api_surface_signature: list[str]
    error_message: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class InteractiveRunner:
    """Keep hardware/world ownership and budgets in the parent across policy turns."""

    def __init__(self, limits: RuntimeLimits | None = None, max_decisions: int = 8,
                 max_total_actions: int = 20, range_margin: float = .15, max_step: float = 1.5):
        if max_decisions < 1 or max_total_actions < 1:
            raise ValueError("Interactive budgets must be positive")
        base = limits or RuntimeLimits()
        self.limits = replace(base, max_actions=min(base.max_actions, max_total_actions))
        self.max_decisions = max_decisions
        self.range_margin = range_margin
        self.max_step = max_step

    def run(self, task: Task, provider, *, backend=None, registry: CapabilityRegistry | None = None,
            observe: Callable[[RobotRuntime], dict] = sample_local_observation) -> InteractiveResult:
        started = time.perf_counter()
        runtime = RobotRuntime(backend or VirtualBackend(task.world, task.initial, self.limits),
                               self.limits, registry)
        runtime.registry, guard = guard_navigation_registry(
            runtime.registry, margin=self.range_margin, max_step=self.max_step)
        evaluator = TaskEvaluator()
        coder = AgentCoder(provider)
        executor = PolicyExecutor(self.limits)
        decisions: list[DecisionRecord] = []
        error_type = error_message = None
        llm_ms = 0.0
        input_tokens = output_tokens = None
        final = runtime.snapshot()
        task_success = evaluator.evaluate(task, final)

        for index in range(1, self.max_decisions + 1):
            if task_success:
                break
            observation = {}
            policy = ""
            response = None
            execution_ms = 0.0
            generation_ms = 0.0
            trace_start = len(runtime.trace)
            check_start = len(guard.checks) if guard else 0
            try:
                observation = observe(runtime)
                observation["remaining_decisions"] = self.max_decisions - index + 1
                observation["remaining_actions"] = self.limits.max_actions - runtime.snapshot().action_count
                observation["navigation_limits"] = {
                    "max_actions_per_decision": 1, "max_forward_step_m": self.max_step,
                    "range_margin_m": self.range_margin,
                    "safe_forward_distance_m": round(max(0.0, min(
                        self.max_step, observation.get("front_distance_m", 0) - self.range_margin)), 9),
                }
                if decisions:
                    previous = decisions[-1]
                    observation["previous_step"] = {"success": previous.success,
                                                    "error_type": previous.error_type,
                                                    "error_message": previous.error_message,
                                                    "actions_after": previous.actions_after}
                    observation["recent_steps"] = [
                        {"pose_before": step.observation.get("pose_estimate"),
                         "front_distance_before_m": step.observation.get("front_distance_m"),
                         "policy": step.policy, "pose_after": step.final_pose,
                         "success": step.success, "error_type": step.error_type}
                        for step in decisions[-4:]]
                generation_started = time.perf_counter()
                try:
                    policy, response = coder.generate_from_observation(task.instruction, observation, runtime.registry)
                finally:
                    generation_ms = (time.perf_counter() - generation_started) * 1000
                    llm_ms += generation_ms
                if response.input_tokens is not None:
                    input_tokens = (input_tokens or 0) + response.input_tokens
                if response.output_tokens is not None:
                    output_tokens = (output_tokens or 0) + response.output_tokens
                validate_decision_policy(policy, runtime.registry)
                runtime.begin_decision(1)
                try:
                    outcome = executor.execute(policy, task.world, task.initial, runtime=runtime)
                finally:
                    runtime.end_decision()
                execution_ms = outcome.execution_ms
                error_type = outcome.error_type
                error_message = outcome.error_message
                final = outcome.final_state
                task_success = outcome.success and evaluator.evaluate(task, final)
                success = outcome.success
            except Exception as exc:
                error_type = type(exc).__name__
                error_message = str(exc)
                success = False
                final = runtime.snapshot()
            decisions.append(DecisionRecord(index, observation, policy, success, error_type,
                                            generation_ms, execution_ms,
                                            final.action_count, list(runtime.trace[trace_start:]),
                                            error_message, asdict(final.pose),
                                            list(guard.checks[check_start:]) if guard else [],
                                            response.ttft_ms if response else None))
            if not success:
                runtime.emergency_stop()
                final = runtime.snapshot()
                decisions[-1].trace = list(runtime.trace[trace_start:])
                if error_type != "MotionSafetyError":
                    break
        else:
            if not task_success:
                error_type = "DecisionLimitExceeded"
                error_message = "Task did not complete within the decision budget"

        if not task_success and error_type == "DecisionLimitExceeded":
            runtime.emergency_stop()
            final = runtime.snapshot()
        return InteractiveResult(task.id, task_success, error_type, final, decisions,
                                 list(runtime.trace), (time.perf_counter() - started) * 1000,
                                 llm_ms, input_tokens, output_tokens,
                                 runtime.registry.api_surface_signature(), error_message)
