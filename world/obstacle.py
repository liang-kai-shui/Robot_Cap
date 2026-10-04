from dataclasses import dataclass


@dataclass(frozen=True)
class Obstacle:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Obstacle dimensions must be positive")

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.x + self.width and self.y <= y <= self.y + self.height
