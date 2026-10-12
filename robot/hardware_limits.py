"""Integer wire units for supervised PARAM1 firmware; no silent rounding."""
import math
from runtime.errors import RobotActionError

MOVE_MIN_M, MOVE_MAX_M = 0.05, 0.30
TURN_MIN_DEG, TURN_MAX_DEG = 15.0, 90.0
MAX_MOTION_CALLS = 3
MAX_TASK_ACTIONS = MAX_MOTION_CALLS + 1  # Reserve the final STOP.


def _units(value, factor, minimum, maximum, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RobotActionError(f"{name} must be numeric")
    try:
        valid = math.isfinite(value) and minimum <= abs(value) <= maximum
    except OverflowError:
        valid = False
    if not valid:
        raise RobotActionError(f"{name} must be finite, with magnitude {minimum}..{maximum}")
    scaled = value * factor
    integer = round(scaled)
    if abs(scaled-integer) > 1e-6:
        raise RobotActionError(f"{name} resolution is {1/factor:g}; no silent rounding")
    return integer


def move_mm(distance):
    mm = _units(distance, 1000, MOVE_MIN_M, MOVE_MAX_M, "forward distance (m)")
    if mm < 0:
        raise RobotActionError("Reverse distance is not supported")
    return mm


def turn_tenths(angle):
    return _units(angle, 10, TURN_MIN_DEG, TURN_MAX_DEG, "relative angle (deg)")
