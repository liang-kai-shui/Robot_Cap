import math
from dataclasses import dataclass, field
from world.geometry import NUMERIC_DECIMALS, ray_box_distance, segment_hits_box
from world.obstacle import Obstacle
from runtime.errors import CollisionError, WorldBoundaryError
from robot.state import Pose


@dataclass(frozen=True)
class VirtualWorld:
    width: float = 10.0
    height: float = 10.0
    obstacles: tuple[Obstacle, ...] = field(default_factory=tuple)
    clearance: float = 0.0

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0 or not 0 <= self.clearance < min(self.width, self.height) / 2:
            raise ValueError("World dimensions must be positive")
        for box in self.obstacles:
            if box.x < 0 or box.y < 0 or box.x + box.width > self.width or box.y + box.height > self.height:
                raise ValueError("Obstacle outside world")

    def _inflated(self, box: Obstacle) -> Obstacle:
        """Conservative square-footprint clearance around an obstacle."""
        c = self.clearance
        return Obstacle(box.x - c, box.y - c, box.width + 2 * c, box.height + 2 * c)

    def check_pose(self, pose: Pose) -> None:
        c = self.clearance
        if not (c <= pose.x <= self.width - c and c <= pose.y <= self.height - c):
            raise WorldBoundaryError("Robot crossed world boundary")
        if any(self._inflated(box).contains(pose.x, pose.y) for box in self.obstacles):
            raise CollisionError("Robot entered obstacle")

    def check_motion(self, start: Pose, end: Pose) -> None:
        self.check_pose(end)
        if any(segment_hits_box(start.x, start.y, end.x, end.y, self._inflated(box)) for box in self.obstacles):
            raise CollisionError("Robot path intersects obstacle")

    def distance_ahead(self, pose: Pose) -> float:
        self.check_pose(pose)
        angle = math.radians(pose.heading)
        dx, dy = math.cos(angle), math.sin(angle)
        # A ray starting inside the world exits through one of its four sides.
        distances = []
        c = self.clearance
        if dx > 1e-12: distances.append((self.width - c - pose.x) / dx)
        if dx < -1e-12: distances.append((c - pose.x) / dx)
        if dy > 1e-12: distances.append((self.height - c - pose.y) / dy)
        if dy < -1e-12: distances.append((c - pose.y) / dy)
        distances.extend(ray_box_distance(pose.x, pose.y, dx, dy, self._inflated(box)) for box in self.obstacles)
        return round(min(distances), NUMERIC_DECIMALS)
