"""Opt-in forward-range guard for the current navigation experiment."""
import math
import time
from dataclasses import dataclass, field
from typing import Callable
from robot.capabilities import CapabilityRegistry
from runtime.errors import MotionSafetyError


@dataclass
class ForwardRangeGuard:
    """Use a fresh registered sensor reading before motion, without a world map.

    Sensor and motion handlers share the action's cancellation event and deadline.
    The margin is an experimental setting, not a calibrated physical stopping distance.
    """

    move_handler: Callable
    range_handler: Callable | None
    margin: float = .15
    max_step: float = 1.5
    range_limit: float = 2.0
    checks: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if not all(math.isfinite(value) for value in (self.margin, self.max_step, self.range_limit)):
            raise ValueError("Navigation guard settings must be finite")
        if self.margin < 0 or min(self.max_step, self.range_limit) <= 0:
            raise ValueError("Invalid navigation guard settings")

    def __call__(self, *, distance, cancel_event, deadline, **arguments):
        check = {"timestamp": time.time(), "requested_distance_m": distance,
                 "front_distance_m": None, "allowed_distance_m": 0.0, "accepted": False}
        self.checks.append(check)
        if distance < 0:
            raise MotionSafetyError("Backward motion has no registered rear-range guard; turn and observe first")
        if self.range_handler is None:
            raise MotionSafetyError("Forward motion requires a registered distance observation")
        reading = self.range_handler(cancel_event=cancel_event, deadline=deadline)
        if isinstance(reading, bool) or not isinstance(reading, (int, float)) or not math.isfinite(reading) or reading < 0:
            raise MotionSafetyError("Forward range observation is invalid")
        check["front_distance_m"] = min(self.range_limit, reading)
        allowed = max(0.0, min(self.max_step, check["front_distance_m"] - self.margin))
        check["allowed_distance_m"] = allowed
        if distance > allowed + 1e-9:
            raise MotionSafetyError(f"Forward move {distance:g} m exceeds fresh guarded range {allowed:g} m")
        check["accepted"] = True
        return self.move_handler(distance=distance, cancel_event=cancel_event, deadline=deadline, **arguments)


def guard_navigation_registry(registry: CapabilityRegistry, *, margin=.15, max_step=1.5):
    """Copy registrations and wrap only the experiment's implemented move capability.

    Generic registry, validator, proxy and dispatcher remain unaware of this motion policy.
    Caller-owned registrations and their handlers are not mutated.
    """
    guarded = CapabilityRegistry()
    move = registry.get("motion.move")
    sensor = registry.get("sensors.get_distance")
    guard = (ForwardRangeGuard(move.handler, sensor.handler if sensor else None, margin, max_step)
             if move else None)
    for spec in registry.list_visible():
        item = registry.get(spec.canonical_id)
        guarded.register(spec, guard if item is move else item.handler,
                         item.validator, item.emergency_stop)
    return guarded, guard
