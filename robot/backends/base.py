"""Backend interface; implementations only live in the trusted process."""
from robot.base import RobotBase


class RobotBackend(RobotBase):
    """Backend contract, including an unconditional safety stop."""

    def emergency_stop(self) -> None:
        """Stop independently of normal policy action limits."""
        raise NotImplementedError
