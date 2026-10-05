"""Physical backend contract placeholder; no device transport is implemented."""
from robot.backends.base import RobotBackend


class PhysicalBackend(RobotBackend):
    """Future hardware contract: bounded I/O, cooperative cancellation, independent stop.

    Each registered action handler must accept cancel_event and deadline and
    check both during I/O. emergency_stop must work while any handler is active.
    No physical transport is implemented here.
    """
