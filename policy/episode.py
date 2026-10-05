"""A record for future policy reuse, without retrieval at runtime."""
from dataclasses import asdict, dataclass
from typing import Any
from uuid import uuid4
from robot.capabilities import API_VERSION, capability_signature


@dataclass
class EpisodeRecord:
    """Serializable execution episode and API compatibility metadata."""

    episode_id: str
    task_id: str
    instruction: str
    policy: str
    api_version: str
    capability_signature: list[str]
    initial_state: dict
    final_state: dict
    execution_success: bool
    task_success: bool
    error_type: str | None
    trace: list[dict]
    metrics: dict[str, Any]

    @classmethod
    def from_run(cls, task, result) -> "EpisodeRecord":
        """Build an episode from a completed task run."""
        return cls(uuid4().hex, task.id, task.instruction, result.policy, API_VERSION,
                   capability_signature(), asdict(result.initial_state), asdict(result.final_state),
                   result.execution_success, result.task_success, result.error_type,
                   result.trace, asdict(result.metrics))

    def to_dict(self) -> dict:
        """Convert to plain JSON data."""
        return asdict(self)
