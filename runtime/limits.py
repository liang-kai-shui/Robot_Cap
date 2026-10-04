from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeLimits:
    timeout_seconds: float = 5.0
    max_actions: int = 100
    max_move_distance: float = 2.0
    max_turn_angle: float = 180.0

    def __post_init__(self):
        if min(self.timeout_seconds, self.max_actions, self.max_move_distance, self.max_turn_angle) <= 0:
            raise ValueError("Runtime limits must be positive")
