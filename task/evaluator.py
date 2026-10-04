import math
from robot.state import RobotState
from task.task import Task
from world.world import VirtualWorld


class TaskEvaluator:
    def evaluate(self, task: Task, state: RobotState) -> bool:
        return self._check(task.success, state, task.world)

    def _check(self, spec: dict, state: RobotState, world: VirtualWorld) -> bool:
        kind = spec.get("type")
        if kind == "pose_near":
            return math.hypot(state.pose.x - spec["x"], state.pose.y - spec["y"]) <= spec.get("tolerance", 0.05)
        if kind == "heading_near":
            delta = (state.pose.heading - spec["heading"] + 180) % 360 - 180
            return abs(delta) <= spec.get("tolerance", 1.0)
        if kind == "distance_range":
            distance = world.distance_ahead(state.pose)
            return spec.get("min", -math.inf) <= distance <= spec.get("max", math.inf)
        if kind == "stopped":
            return state.stopped == spec.get("value", True)
        if kind == "compound_all":
            return all(self._check(item, state, world) for item in spec["conditions"])
        if kind == "compound_any":
            return any(self._check(item, state, world) for item in spec["conditions"])
        if kind == "unverified":
            return False
        raise ValueError(f"Unknown success condition: {kind}")
