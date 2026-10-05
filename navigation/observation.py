"""Range semantics for the current simulator's configuration-space sensor."""
import math
import time
from dataclasses import asdict, dataclass
from robot.state import Pose


@dataclass(frozen=True)
class RangeObservation:
    pose: Pose
    distance_m: float
    max_range_m: float
    hit: bool
    valid: bool
    timestamp: float
    capability_id: str
    space: str = "configuration"

    def __post_init__(self):
        values = (self.pose.x, self.pose.y, self.pose.heading, self.distance_m,
                  self.max_range_m, self.timestamp)
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   and math.isfinite(value) for value in values):
            raise ValueError("Range observation numbers must be finite")
        if self.max_range_m <= 0 or not 0 <= self.distance_m <= self.max_range_m:
            raise ValueError("Invalid range interval")
        if not isinstance(self.hit, bool) or not isinstance(self.valid, bool):
            raise ValueError("Range flags must be booleans")
        if self.space != "configuration":
            raise ValueError("Raw physical range needs an explicit footprint adapter")

    @classmethod
    def from_reading(cls, reading, pose, capability_id, max_range_m=2.0):
        valid = (isinstance(reading, (int, float)) and not isinstance(reading, bool)
                 and math.isfinite(reading) and reading >= 0)
        return cls(pose, min(max_range_m, reading) if valid else 0.0, max_range_m,
                   bool(valid and reading <= max_range_m), valid, time.time(), capability_id)

    @classmethod
    def from_dict(cls, data):
        return cls(**{**data, "pose": Pose(**data["pose"])})

    def to_dict(self):
        return asdict(self)

    def require_fresh(self, max_age_seconds=2.0):
        if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("Observation age limit must be positive and finite")
        age = time.time() - self.timestamp
        if not self.valid or age < -0.1 or age > max_age_seconds:
            raise ValueError("Invalid or stale range observation")
