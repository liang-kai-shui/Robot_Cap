from dataclasses import dataclass


@dataclass
class RunMetrics:
    model: str | None = None
    llm_total_ms: float | None = None
    llm_ttft_ms: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    policy_chars: int = 0
    policy_lines: int = 0
    validation_ms: float = 0.0
    execution_ms: float = 0.0
    evaluation_ms: float = 0.0
    presentation_ms: float | None = None
    confirmation_wait_ms: float | None = None
    total_ms: float = 0.0
    action_count: int = 0
    execution_success: bool = False
    task_success: bool = False
    error_type: str | None = None
