"""Deterministic backend for runtime failure-path tests."""
import time
from robot.backends.base import RobotBackend
from robot.state import Pose, RobotState


class MockRobotBackend(RobotBackend):
    """Count calls and optionally fail or exceed a deadline."""

    def __init__(self, error: Exception | None = None, delay_seconds: float = 0.0):
        self.error, self.delay_seconds = error, delay_seconds
        self.calls = 0
        self.emergency_stops = 0
        self._state = RobotState(Pose(0, 0, 0), stopped=True)

    def _act(self, name: str) -> None:
        self.calls += 1
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if self.error:
            raise self.error
        self._state = RobotState(self._state.pose, True, False, name.upper(), self.calls)

    def move(self, distance: float, speed: float | None = None) -> None:
        """Record a linear action."""
        self._act("move")

    def turn(self, angle: float, speed: float | None = None) -> None:
        """Record a turn action."""
        self._act("turn")

    def stop(self) -> None:
        """Record a normal stop."""
        self._act("stop")

    def emergency_stop(self) -> None:
        """Record a forced stop."""
        self.emergency_stops += 1
        self._state = RobotState(self._state.pose, True, False, "EMERGENCY_STOP", self.calls)

    def get_pose(self) -> Pose:
        """Return mock pose."""
        return self._state.pose

    def get_distance(self) -> float:
        """Return mock distance."""
        return 1.0

    def get_state(self) -> RobotState:
        """Return mock state."""
        return self._state

    def snapshot(self) -> RobotState:
        """Return state without a recorded call."""
        return self._state
