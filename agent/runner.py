import time
from robot.state import Pose, RobotState
from robot.capabilities import CapabilityRegistry, DEFAULT_REGISTRY
from agent.coder import AgentCoder
from metrics.collector import save_run
from metrics.models import RunMetrics
from providers.base import LLMProvider
from runtime.executor import PolicyExecutor
from runtime.limits import RuntimeLimits
from runtime.result import ExecutionResult
from runtime.validator import PolicyValidator
from task.evaluator import TaskEvaluator
from task.task import Task


def run_task(task: Task, provider: LLMProvider, limits: RuntimeLimits | None = None,
             run_dir: str | None = "runs", on_validated=None, on_policy=None,
             registry: CapabilityRegistry | None = None) -> ExecutionResult:
    execution_registry = registry
    registry = registry if registry is not None else DEFAULT_REGISTRY
    started = time.perf_counter()
    initial = RobotState(Pose(task.initial.x, task.initial.y, task.initial.heading % 360))
    metrics = RunMetrics()
    policy = ""
    final, logs, trace = initial, [], []
    api_surface_signature = registry.api_surface_signature()
    execution_success = task_success = False
    error_type = error_message = None
    try:
        if task.fixed_policy is None:
            policy, response = AgentCoder(provider).generate(task, registry)
            metrics.model = response.model
            metrics.llm_total_ms = response.total_ms
            metrics.llm_ttft_ms = response.ttft_ms
            metrics.input_tokens = response.input_tokens
            metrics.output_tokens = response.output_tokens
        else:
            policy = task.fixed_policy
            metrics.model = "fixed-policy"
        metrics.policy_chars = len(policy)
        metrics.policy_lines = len(policy.splitlines())
        validation_started = time.perf_counter()
        try:
            PolicyValidator(registry).validate(policy)
        finally:
            metrics.validation_ms = (time.perf_counter() - validation_started) * 1000
        if on_policy is not None:
            presentation_started = time.perf_counter()
            try:
                on_policy(policy)
            finally:
                metrics.presentation_ms = (time.perf_counter() - presentation_started) * 1000
        should_execute = True
        if on_validated is not None:
            confirmation_started = time.perf_counter()
            try:
                should_execute = on_validated(policy)
            finally:
                metrics.confirmation_wait_ms = (time.perf_counter() - confirmation_started) * 1000
        if not should_execute:
            error_type, error_message = "ExecutionDeclined", "Execution declined by user"
        else:
            outcome = PolicyExecutor(limits).execute(policy, task.world, task.initial,
                                                     validated=True, registry=execution_registry)
            metrics.execution_ms = outcome.execution_ms
            execution_success = outcome.success
            error_type, error_message = outcome.error_type, outcome.error_message
            final, logs = outcome.final_state, outcome.logs
            trace = outcome.trace
            api_surface_signature = outcome.api_surface_signature
            metrics.action_count = final.action_count
            evaluation_started = time.perf_counter()
            try:
                task_success = execution_success and TaskEvaluator().evaluate(task, final)
            finally:
                metrics.evaluation_ms = (time.perf_counter() - evaluation_started) * 1000
    except Exception as exc:
        error_type, error_message = type(exc).__name__, str(exc)
    metrics.execution_success, metrics.task_success, metrics.error_type = execution_success, task_success, error_type
    metrics.total_ms = ((time.perf_counter() - started) * 1000
                        - (metrics.presentation_ms or 0.0) - (metrics.confirmation_wait_ms or 0.0))
    result = ExecutionResult(execution_success, task_success, error_type, error_message,
                             initial, final, logs, metrics, policy, trace, api_surface_signature)
    if run_dir is not None:
        save_run(task, result, run_dir)
    return result
