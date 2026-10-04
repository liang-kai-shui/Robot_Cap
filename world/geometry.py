import math
from world.obstacle import Obstacle


def ray_box_distance(x: float, y: float, dx: float, dy: float, box: Obstacle) -> float:
    """Distance along a unit ray to an axis-aligned rectangle, or infinity."""
    near, far = -math.inf, math.inf
    for origin, direction, low, high in ((x, dx, box.x, box.x + box.width), (y, dy, box.y, box.y + box.height)):
        if abs(direction) < 1e-12:
            if origin < low or origin > high:
                return math.inf
            continue
        a, b = (low - origin) / direction, (high - origin) / direction
        near, far = max(near, min(a, b)), min(far, max(a, b))
    return max(0.0, near) if far >= max(0.0, near) else math.inf


def segment_hits_box(x: float, y: float, end_x: float, end_y: float, box: Obstacle) -> bool:
    length = math.hypot(end_x - x, end_y - y)
    return length > 0 and ray_box_distance(x, y, (end_x-x)/length, (end_y-y)/length, box) <= length + 1e-10
