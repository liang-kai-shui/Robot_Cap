from dataclasses import dataclass
from typing import Any
from robot.state import Pose
from world.world import VirtualWorld


@dataclass(frozen=True)
class Task:
    id: str
    instruction: str
    initial: Pose
    success: dict[str, Any]
    world: VirtualWorld = VirtualWorld()
    fixed_policy: str | None = None
