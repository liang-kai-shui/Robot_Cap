"""Software-only receding-horizon task session with a persistent trusted runtime."""
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Callable
from agent.coder import AgentCoder
from agent.observations import sample_local_observation
from robot.backends.virtual import VirtualBackend
from robot.capabilities import CapabilityRegistry
from robot.runtime import RobotRuntime
from robot.state import RobotState
from runtime.executor import PolicyExecutor
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

    def to_dict(self) -> dict:
        return asdict(self)


class InteractiveRunner:
    """Keep hardware/world ownership and budgets in the parent across policy turns."""

    def __init__(self, limits: RuntimeLimits | None = None, max_decisions: int = 8,
                 max_total_actions: int = 20, max_actions_per_decision: int = 3):
        if max_decisions < 1 or max_total_actions < 1 or max_actions_per_decision < 1:
            raise ValueError("Interactive budgets must be positive")
        base = limits or RuntimeLimits()
        self.limits = replace(base, max_actions=min(base.max_actions, max_total_actions))
        self.max_decisions = max_decisions
        self.max_actions_per_decision = max_actions_per_decision

    def run(self, task: Task, provider, *, backend=None, registry: CapabilityRegistry | None = None,
            observe: Callable[[RobotRuntime], dict] = sample_local_observation) -> InteractiveResult:
        started = time.perf_counter()
        runtime = RobotRuntime(backend or VirtualBackend(task.world, task.initial, self.limits),
                               self.limits, registry)
        evaluator = TaskEvaluator()
        coder = AgentCoder(provider)
        executor = PolicyExecutor(self.limits)
        decisions: list[DecisionRecord] = []
        error_type = None
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
            trace_start = len(runtime.trace)
            try:
                observation = observe(runtime)
                observation["remaining_decisions"] = self.max_decisions - index + 1
                observation["remaining_actions"] = self.limits.max_actions - runtime.snapshot().action_count
                if decisions:
                    previous = decisions[-1]
                    observation["previous_step"] = {"success": previous.success,
                                                    "error_type": previous.error_type,
                                                    "actions_after": previous.actions_after}
                policy, response = coder.generate_from_observation(task.instruction, observation, runtime.registry)
                llm_ms += response.total_ms
                if response.input_tokens is not None:
                    input_tokens = (input_tokens or 0) + response.input_tokens
                if response.output_tokens is not None:
                    output_tokens = (output_tokens or 0) + response.output_tokens
                runtime.begin_decision(self.max_actions_per_decision)
                try:
                    outcome = executor.execute(policy, task.world, task.initial, runtime=runtime)
                finally:
                    runtime.end_decision()
                execution_ms = outcome.execution_ms
                error_type = outcome.error_type
                final = outcome.final_state
                task_success = outcome.success and evaluator.evaluate(task, final)
                success = outcome.success
            except Exception as exc:
                error_type = type(exc).__name__
                success = False
                final = runtime.snapshot()
            decisions.append(DecisionRecord(index, observation, policy, success, error_type,
                                            response.total_ms if response else None, execution_ms,
                                            final.action_count, list(runtime.trace[trace_start:])))
            if not success:
                runtime.emergency_stop()
                final = runtime.snapshot()
                decisions[-1].trace = list(runtime.trace[trace_start:])
                break

        if not task_success and error_type is None:
            error_type = "DecisionLimitExceeded"
            runtime.emergency_stop()
            final = runtime.snapshot()
        return InteractiveResult(task.id, task_success, error_type, final, decisions,
                                 list(runtime.trace), (time.perf_counter() - started) * 1000,
                                 llm_ms, input_tokens, output_tokens,
                                 runtime.registry.api_surface_signature())
