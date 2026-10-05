"""Trusted backend safety contract, independent of capability categories."""
from abc import ABC, abstractmethod


class RobotBackend(ABC):
    """Handlers must honor cancel_event/deadline; stop must be independently callable.

    Physical implementations must use bounded I/O and allow emergency_stop while
    an ordinary handler is waiting. Python threads cannot forcibly cancel I/O.
    """

    @abstractmethod
    def emergency_stop(self) -> None:
        """Stop independently of normal action handlers and their locks."""
