from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeLimits:
    timeout_seconds: float = 5.0
    policy_timeout_seconds: float | None = None
    max_actions: int = 100
    max_move_distance: float = 2.0
    max_turn_angle: float = 180.0
    max_linear_speed: float = 0.5
    max_angular_speed: float = 90.0
    default_action_timeout_seconds: float = 2.0
    communication_timeout_seconds: float = 2.0

    @property
    def effective_policy_timeout_seconds(self) -> float:
        """Use the explicit compute budget or the legacy timeout_seconds key."""
        return self.policy_timeout_seconds if self.policy_timeout_seconds is not None else self.timeout_seconds

    def __post_init__(self):
        if min(self.timeout_seconds, self.effective_policy_timeout_seconds,
               self.max_actions, self.max_move_distance, self.max_turn_angle,
               self.max_linear_speed, self.max_angular_speed, self.default_action_timeout_seconds,
               self.communication_timeout_seconds) <= 0:
            raise ValueError("Runtime limits must be positive")
