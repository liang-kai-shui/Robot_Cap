from abc import ABC, abstractmethod
from robot.state import Pose, RobotState


class RobotBase(ABC):
    @abstractmethod
    def move(self, distance: float, speed: float | None = None) -> None: ...

    @abstractmethod
    def turn(self, angle: float, speed: float | None = None) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def get_pose(self) -> Pose: ...

    @abstractmethod
    def get_distance(self) -> float: ...

    @abstractmethod
    def get_state(self) -> RobotState: ...
