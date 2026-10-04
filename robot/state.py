from dataclasses import dataclass


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    heading: float


@dataclass(frozen=True)
class RobotState:
    pose: Pose
    stopped: bool = False
    collision: bool = False
    last_action: str | None = None
    action_count: int = 0
