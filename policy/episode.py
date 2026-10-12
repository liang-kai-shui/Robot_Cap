"""Shared execution provenance for simulation and feedback-verified hardware reuse."""
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4
from robot.capabilities import API_VERSION


@dataclass
class EpisodeRecord:
    """Serializable execution episode and API compatibility metadata."""

    episode_id: str
    task_id: str
    instruction: str
    policy: str
    api_version: str
    api_surface_signature: list[str]
    used_capabilities: list[str]
    initial_state: dict
    final_state: dict
    execution_success: bool
    task_success: bool | None
    error_type: str | None
    trace: list[dict]
    metrics: dict[str, Any]

    @classmethod
    def from_run(cls, task, result) -> "EpisodeRecord":
        """Build an episode from a completed task run."""
        used = list(dict.fromkeys(
            f"{item['capability_id']}@{item['capability_version']}"
            for item in result.trace
            if item.get("kind") in ("action", "observation")
            and item.get("phase") == "started" and item.get("capability_version")
        ))
        return cls(uuid4().hex, task.id, task.instruction, result.policy, API_VERSION,
                   result.api_surface_signature, used, asdict(result.initial_state), asdict(result.final_state),
                   result.execution_success, result.task_success, result.error_type,
                   result.trace, asdict(result.metrics))

    def to_dict(self) -> dict:
        """Convert to plain JSON data."""
        return asdict(self)

    @classmethod
    def from_physical(cls, *, instruction, policy, registry, initial_state, final_state,
                      trace, execution_success, error=None, error_type=None, metrics=None, task_id=None):
        """Hardware states are actual feedback dictionaries; no synthetic ground pose."""
        from copy import deepcopy
        from metrics.models import RunMetrics
        extra = deepcopy(metrics or {})
        timing = extra.get('timings_ms', {})
        generation = extra.get('generation') or {}
        diagnostics = extra.get('diagnostics') or generation
        usage = diagnostics.get('usage', generation)
        skipped = diagnostics.get('model_call_skipped', False)
        inherited = diagnostics.get('planning_request_inherited', False)
        planning_calls = 0 if skipped or inherited else int(diagnostics.get('model_call_started',
                                                                timing.get('model_request', 0) > 0))
        reviews = [review for review in extra.get('reviews', []) if review.get('model_call_started')]
        model_usage = ([usage] if planning_calls else []) + reviews

        def token_total(name):
            # A failed request or an absent provider usage field is unknown, not zero.
            values = [call.get(name) for call in model_usage]
            return sum(values) if all(type(value) is int for value in values) else None

        failure = error_type or (type(error).__name__ if isinstance(error, BaseException)
                                else str(error).split(':', 1)[0] if error else None)
        standard = RunMetrics(
            model=diagnostics.get('model', generation.get('model')),
            llm_total_ms=(timing.get('model_request', 0) if planning_calls else 0) + sum(review.get('elapsed_ms', 0) for review in reviews),
            llm_ttft_ms=usage.get('ttft_ms') if planning_calls else None,
            input_tokens=token_total('input_tokens'), output_tokens=token_total('output_tokens'),
            policy_chars=len(policy), policy_lines=len(policy.splitlines()),
            validation_ms=timing.get('policy_validation', 0), execution_ms=timing.get('policy_execution', 0),
            total_ms=timing.get('task_total', 0),
            action_count=sum(event.get('kind') == 'action' and event.get('phase') == 'completed' for event in trace),
            execution_success=bool(execution_success), task_success=None, error_type=failure)
        extra = {**asdict(standard), **extra, 'llm_calls': planning_calls + len(reviews),
                 'planning_llm_calls': planning_calls, 'review_llm_calls': len(reviews),
                 'planning_request_inherited': bool(inherited)}
        used = list(dict.fromkeys(f"{item['capability_id']}@{item['capability_version']}"
                                 for item in trace if item.get('kind') in ('action', 'observation')
                                 and item.get('phase') == 'started' and item.get('capability_version')))
        return cls(uuid4().hex, task_id or uuid4().hex, instruction, policy, API_VERSION,
                   registry.api_surface_signature(), used, deepcopy(initial_state), deepcopy(final_state),
                   bool(execution_success), None, failure, deepcopy(trace), extra)
