from dataclasses import dataclass, field
from robot.state import RobotState
from metrics.models import RunMetrics


@dataclass
class ExecutionResult:
    execution_success: bool
    task_success: bool
    error_type: str | None
    error_message: str | None
    initial_state: RobotState
    final_state: RobotState
    logs: list[dict] = field(default_factory=list)
    metrics: RunMetrics = field(default_factory=RunMetrics)
    policy: str = ""
    trace: list[dict] = field(default_factory=list)
    api_surface_signature: list[str] = field(default_factory=list)
