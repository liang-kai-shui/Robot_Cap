"""Safety checks bound to the current motion capabilities, not runtime core."""
import math
from runtime.errors import (MoveBelowResolutionError, MoveLimitExceeded,
                            RobotActionError, TurnLimitExceeded)
from world.geometry import NUMERIC_RESOLUTION


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a number")
    try:
        return float(value)
    except OverflowError as exc:
        raise RobotActionError(f"{label} is out of range") from exc


def _speed(values, limits, maximum):
    if "speed" not in values or values["speed"] is None:
        return
    try:
        speed = _finite(values["speed"], "speed")
    except TypeError as exc:
        raise RobotActionError(str(exc)) from exc
    if not math.isfinite(speed) or speed <= 0 or speed > maximum:
        raise RobotActionError("speed exceeds safe range")
    values["speed"] = speed


def validate_move(values: dict, limits) -> dict:
    """Validate one finite move and its optional linear speed."""
    distance = _finite(values["distance"], "Move distance")
    if not math.isfinite(distance) or abs(distance) > limits.max_move_distance:
        raise MoveLimitExceeded(f"Move exceeds {limits.max_move_distance} m")
    if 0 < abs(distance) < NUMERIC_RESOLUTION:
        raise MoveBelowResolutionError(f"Move is below {NUMERIC_RESOLUTION:g} m resolution")
    values["distance"] = distance
    _speed(values, limits, limits.max_linear_speed)
    return values


def validate_turn(values: dict, limits) -> dict:
    """Validate one finite turn and its optional angular speed."""
    angle = _finite(values["angle"], "Turn angle")
    if not math.isfinite(angle) or abs(angle) > limits.max_turn_angle:
        raise TurnLimitExceeded(f"Turn exceeds {limits.max_turn_angle} degrees")
    values["angle"] = angle
    _speed(values, limits, limits.max_angular_speed)
    return values
