import math
import time
from robot.base import RobotBase
from robot.state import Pose, RobotState
from runtime.errors import ActionLimitExceeded, MoveLimitExceeded, MoveBelowResolutionError, TurnLimitExceeded, CollisionError
from runtime.limits import RuntimeLimits
from world.geometry import NUMERIC_RESOLUTION
from world.world import VirtualWorld


class VirtualRobot(RobotBase):
    def __init__(self, world: VirtualWorld, pose: Pose, limits: RuntimeLimits | None = None):
        world.check_pose(pose)
        self._world, self._limits = world, limits or RuntimeLimits()
        self._pose = Pose(pose.x, pose.y, pose.heading % 360)
        self._stopped = False
        self._collision = False
        self._last_action = None
        self._action_count = 0
        self.logs: list[dict] = []

    def _record(self, action: str, **details):
        self.logs.append({"time": time.time(), "action": action, **details, "pose": vars(self._pose).copy()})

    def _act(self, action: str):
        if self._action_count >= self._limits.max_actions:
            self._record(action, error="ActionLimitExceeded")
            raise ActionLimitExceeded(f"Maximum {self._limits.max_actions} actions exceeded")
        self._action_count += 1
        self._last_action = action

    def move(self, distance: float) -> None:
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            self._record("MOVE", error="TypeError")
            raise TypeError("Move distance must be a number")
        try:
            distance = float(distance)
        except OverflowError:
            self._record("MOVE", error="OverflowError")
            raise
        if not math.isfinite(distance) or abs(distance) > self._limits.max_move_distance:
            self._record("MOVE", distance=distance, error="MoveLimitExceeded")
            raise MoveLimitExceeded(f"Move exceeds {self._limits.max_move_distance} m")
        if 0 < abs(distance) < NUMERIC_RESOLUTION:
            self._record("MOVE", distance=distance, error="MoveBelowResolutionError")
            raise MoveBelowResolutionError(f"Move is below {NUMERIC_RESOLUTION:g} m resolution")
        self._act("MOVE")
        angle = math.radians(self._pose.heading)
        end = Pose(self._pose.x + distance * math.cos(angle), self._pose.y + distance * math.sin(angle), self._pose.heading)
        try:
            self._world.check_motion(self._pose, end)
        except CollisionError:
            self._collision = True
            self._record("MOVE", distance=distance, error="CollisionError")
            raise
        except Exception as exc:
            self._record("MOVE", distance=distance, error=type(exc).__name__)
            raise
        self._pose = end
        self._stopped = False
        self._record("MOVE", distance=distance)

    def turn(self, angle: float) -> None:
        if isinstance(angle, bool) or not isinstance(angle, (int, float)):
            self._record("TURN", error="TypeError")
            raise TypeError("Turn angle must be a number")
        try:
            angle = float(angle)
        except OverflowError:
            self._record("TURN", error="OverflowError")
            raise
        if not math.isfinite(angle) or abs(angle) > self._limits.max_turn_angle:
            self._record("TURN", angle=angle, error="TurnLimitExceeded")
            raise TurnLimitExceeded(f"Turn exceeds {self._limits.max_turn_angle} degrees")
        self._act("TURN")
        self._pose = Pose(self._pose.x, self._pose.y, (self._pose.heading + angle) % 360)
        self._stopped = False
        self._record("TURN", angle=angle)

    def stop(self) -> None:
        self._act("STOP")
        self._stopped = True
        self._record("STOP")

    def get_pose(self) -> Pose:
        self._record("GET_POSE")
        return self._pose

    def get_distance(self) -> float:
        value = self._world.distance_ahead(self._pose)
        self._record("GET_DISTANCE", distance=value)
        return value

    def get_state(self) -> RobotState:
        self._record("GET_STATE")
        return RobotState(self._pose, self._stopped, self._collision, self._last_action, self._action_count)

    def snapshot(self) -> RobotState:
        return RobotState(self._pose, self._stopped, self._collision, self._last_action, self._action_count)
