"""Virtual backend reusing the existing world and robot physics."""
from robot.backends.base import RobotBackend
from robot.state import Pose, RobotState
from robot.virtual import VirtualRobot
from runtime.limits import RuntimeLimits
from world.world import VirtualWorld


class VirtualBackend(RobotBackend):
    """Trusted adapter over the V0.2 simulator."""

    def __init__(self, world: VirtualWorld, pose: Pose, limits: RuntimeLimits | None = None):
        self.robot = VirtualRobot(world, pose, limits)

    @property
    def logs(self) -> list[dict]:
        """Legacy simulator log entries."""
        return self.robot.logs

    def move(self, distance: float, speed: float | None = None,
             *, cancel_event=None, deadline=None) -> None:
        """Complete one finite linear motion."""
        if cancel_event is not None and cancel_event.is_set():
            return
        self.robot.move(distance, speed)

    def turn(self, angle: float, speed: float | None = None,
             *, cancel_event=None, deadline=None) -> None:
        """Complete one finite turn."""
        if cancel_event is not None and cancel_event.is_set():
            return
        self.robot.turn(angle, speed)

    def stop(self, *, cancel_event=None, deadline=None) -> None:
        """Stop as a normal counted action."""
        self.robot.stop()

    def emergency_stop(self) -> None:
        """Force a stop even after the action budget is exhausted."""
        self.robot._stopped = True
        self.robot._record("EMERGENCY_STOP")

    def record_rejection(self, capability_id: str, request: dict, error: str) -> None:
        """Retain legacy simulator logs for requests rejected by the runtime."""
        if capability_id not in {"motion.move", "motion.turn"}:
            return
        details = dict(request.get("kwargs", {}))
        if request.get("args"):
            key = "distance" if capability_id == "motion.move" else "angle"
            details[key] = request["args"][0]
        self.robot._record(capability_id.rsplit(".", 1)[-1].upper(), **details, error=error)

    def get_pose(self, *, cancel_event=None, deadline=None) -> Pose:
        """Read pose."""
        return self.robot.get_pose()

    def get_distance(self, *, cancel_event=None, deadline=None) -> float:
        """Read forward distance."""
        return self.robot.get_distance()

    def get_state(self, *, cancel_event=None, deadline=None) -> RobotState:
        """Read state."""
        return self.robot.get_state()

    def snapshot(self) -> RobotState:
        """Read state without creating an observation log."""
        return self.robot.snapshot()
