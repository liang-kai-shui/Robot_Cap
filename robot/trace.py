"""JSON-friendly runtime events."""
from dataclasses import asdict, dataclass, field
import time
from typing import Any


@dataclass
class TraceEvent:
    """One action, observation, or safety transition."""

    kind: str
    command_id: int | None
    capability_id: str
    capability_version: str | None
    phase: str
    request: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    error: dict[str, str] | None = None
    state: dict | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """Convert event to a JSON-serializable record."""
        return asdict(self)
