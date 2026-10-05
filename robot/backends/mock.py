"""Deterministic backend for runtime failure-path tests."""
import time
import threading
from runtime.errors import RobotActionTimeoutError
from robot.backends.base import RobotBackend
from robot.state import Pose, RobotState


class MockRobotBackend(RobotBackend):
    """Count calls and optionally fail or exceed a deadline."""

    def __init__(self, error: Exception | None = None, delay_seconds: float = 0.0):
        self.error, self.delay_seconds = error, delay_seconds
        self.calls = 0
        self.emergency_stops = 0
        self.cancellations = 0
        self._lock = threading.Lock()
        self._state = RobotState(Pose(0, 0, 0), stopped=True)

    def _act(self, name: str, cancel_event=None, deadline=None) -> None:
        self.calls += 1
        end = time.monotonic() + self.delay_seconds
        while time.monotonic() < end:
            if cancel_event is not None and cancel_event.is_set():
                self.cancellations += 1
                return
            if deadline is not None and time.monotonic() >= deadline:
                self.cancellations += 1
                raise RobotActionTimeoutError("Mock action reached deadline")
            time.sleep(max(0.0, min(0.005, end - time.monotonic())))
        if cancel_event is not None and cancel_event.is_set():
            self.cancellations += 1
            return
        if self.error:
            raise self.error
        with self._lock:
            self._state = RobotState(self._state.pose, True, False, name.upper(), self.calls)

    def move(self, distance: float, speed: float | None = None,
             *, cancel_event=None, deadline=None) -> None:
        """Record a linear action."""
        self._act("move", cancel_event, deadline)

    def turn(self, angle: float, speed: float | None = None,
             *, cancel_event=None, deadline=None) -> None:
        """Record a turn action."""
        self._act("turn", cancel_event, deadline)

    def stop(self, *, cancel_event=None, deadline=None) -> None:
        """Record a normal stop."""
        self._act("stop", cancel_event, deadline)

    def emergency_stop(self) -> None:
        """Record a forced stop."""
        self.emergency_stops += 1
        with self._lock:
            self._state = RobotState(self._state.pose, True, False, "EMERGENCY_STOP", self.calls)

    def get_pose(self, *, cancel_event=None, deadline=None) -> Pose:
        """Return mock pose."""
        return self._state.pose

    def get_distance(self, *, cancel_event=None, deadline=None) -> float:
        """Return mock distance."""
        return 1.0

    def get_state(self, *, cancel_event=None, deadline=None) -> RobotState:
        """Return mock state."""
        return self._state

    def snapshot(self) -> RobotState:
        """Return state without a recorded call."""
        return self._state
