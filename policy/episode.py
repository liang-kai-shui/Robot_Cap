"""A record for future policy reuse, without retrieval at runtime."""
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
    task_success: bool
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
