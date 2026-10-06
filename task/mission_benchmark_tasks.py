"""Public target catalogues and private ordered-arrival evaluation scenarios."""
from dataclasses import dataclass
from navigation.planner import NavigationGoal
from robot.state import Pose
from task.task import Task
from task.interactive_benchmark_tasks import INTERACTIVE_TASKS
from world.obstacle import Obstacle
from world.world import VirtualWorld


@dataclass(frozen=True)
class MissionScenario:
    task: Task
    public_targets: dict[str, NavigationGoal]
    expected_order: tuple[str, ...]  # Evaluator/reference only, never model input.
    category: str


def mission(identifier, instruction, points, order, initial=Pose(1, 1, 0), obstacles=()):
    catalog = {name: NavigationGoal(*point) for name, point in points.items()}
    last = catalog[order[-1]]
    task = Task(identifier, instruction + " 每个要求访问的点都要停稳。地图未知，使用局部观测避障。",
                initial, {"type": "compound_all", "conditions": [
                    {"type": "pose_near", "x": last.x, "y": last.y, "tolerance": last.tolerance},
                    {"type": "stopped"}]}, VirtualWorld(obstacles=tuple(obstacles), clearance=.1))
    return MissionScenario(task, catalog, tuple(order), "language-sequence")


MISSION_SCENARIOS = (
    *(MissionScenario(task, {"target": NavigationGoal(4, 1)}, ("target",), "paired-H")
      for task in INTERACTIVE_TASKS),
    mission("M1", "先去A，再去B，最后返回起点。", {"A": (3, 1), "B": (3, 3), "start": (1, 1)}, ("A", "B", "start")),
    mission("M2", "先去B，最后去A。", {"A": (3, 1), "B": (3, 3), "start": (1, 1)}, ("B", "A")),
    mission("M3", "去A，然后去B，最后再去一次A。", {"A": (3, 1), "B": (3, 2)}, ("A", "B", "A")),
    mission("M4", "直接去B，不需要访问A，也不用返回起点。", {"A": (2, 1), "B": (3, 2), "start": (1, 1)}, ("B",)),
    mission("M5", "先去A，再去B。", {"A": (3, 2), "B": (4, 1)}, ("A", "B"),
            obstacles=(Obstacle(2, .6, .5, .8),)),
    mission("M6", "先去A，再返回出发点。", {"A": (3, 3), "start": (5, 3), "B": (5, 5)}, ("A", "start"),
            initial=Pose(5, 3, 180)),
)
