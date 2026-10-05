"""Physical backend contract placeholder; no device transport is implemented."""
from robot.backends.base import RobotBackend


class PhysicalBackend(RobotBackend):
    """Marker interface for a future hardware implementation."""
