"""Public navigation goals paired with private worlds used only by the harness."""
from dataclasses import dataclass
from collections import deque
import math
import random
from navigation.planner import NavigationGoal
from robot.state import Pose
from task.interactive_benchmark_tasks import INTERACTIVE_TASKS
from task.task import Task
from world.obstacle import Obstacle
from world.world import VirtualWorld


@dataclass(frozen=True)
class NavigationScenario:
    task: Task
    goal: NavigationGoal
    category: str
    expected_reachable: bool = True  # Evaluation metadata, never given to the navigator.


def scenario(identifier, start, goal, obstacles=(), category="static", reachable=True):
    target = NavigationGoal(*goal)
    task = Task(identifier, f"到达({target.x},{target.y})并停稳，地图未知。", start,
                {"type": "compound_all", "conditions": [
                    {"type": "pose_near", "x": target.x, "y": target.y, "tolerance": target.tolerance},
                    {"type": "stopped"}]},
                VirtualWorld(obstacles=tuple(obstacles), clearance=.1))
    return NavigationScenario(task, target, category, reachable)


NAVIGATION_SCENARIOS = (
    *(NavigationScenario(task, NavigationGoal(4, 1), "paired-H") for task in INTERACTIVE_TASKS),
    scenario("G1", Pose(1, 1, 0), (6, 3), category="different-goal"),
    scenario("G2", Pose(7, 7, 180), (3, 5), category="different-start"),
    scenario("G3", Pose(1, 1, 0), (4.08, 1.08), category="off-grid-goal"),
    scenario("W1", Pose(1, 2, 0), (5, 2), (Obstacle(2.5, .5, .5, 3),), "wall-detour"),
    scenario("W2", Pose(1, 1, 90), (6, 4),
             (Obstacle(2, .5, .5, 2), Obstacle(4, 2.5, .5, 3)), "staggered-walls"),
    scenario("U1", Pose(3, 2, 90), (3, 6),
             (Obstacle(2, 1, .3, 3), Obstacle(3.7, 1, .3, 3), Obstacle(2, 3.7, 2, .3)), "dead-end"),
    scenario("C1", Pose(1, 2, 0), (7, 2),
             (Obstacle(2, .5, 4, .8), Obstacle(2, 2.7, 4, .8)), "corridor"),
    scenario("X1", Pose(1, 1, 0), (4, 1),
             (Obstacle(2, 0, .5, 10),), "unreachable", False),
)


def _oracle_reachable(world, start, goal):
    """Private generator check; never called by navigation or exposed as a hint."""
    initial = round(start.x * 4), round(start.y * 4)
    target = round(goal[0] * 4), round(goal[1] * 4)
    pending, seen = deque([initial]), {initial}
    while pending:
        cell = pending.popleft()
        if cell == target:
            return True
        for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            neighbor = cell[0] + dx, cell[1] + dy
            if neighbor in seen or not all(1 <= value <= 39 for value in neighbor):
                continue
            seen.add(neighbor)
            try:
                world.check_motion(Pose(cell[0] / 4, cell[1] / 4, 0),
                                   Pose(neighbor[0] / 4, neighbor[1] / 4, 0))
            except Exception:
                continue
            pending.append(neighbor)
    return False


def generate_navigation_scenarios(count, seed):
    """Seeded evaluation scenes, sampled without consulting the navigator's behavior."""
    if count < 1:
        raise ValueError("Random scene count must be positive")
    rng = random.Random(seed)
    scenarios = []
    for _ in range(count * 100):
        boxes = []
        for _ in range(rng.randint(2, 6)):
            width, height = rng.randint(2, 6) / 4, rng.randint(2, 6) / 4
            boxes.append(Obstacle(rng.randint(2, int((9.5 - width) * 4)) / 4,
                                  rng.randint(2, int((9.5 - height) * 4)) / 4, width, height))
        start = Pose(rng.randint(2, 38) / 4, rng.randint(2, 38) / 4, rng.choice((0, 90, 180, 270)))
        goal = rng.randint(2, 38) / 4, rng.randint(2, 38) / 4
        item = scenario(f"R{len(scenarios)+1:03}", start, goal, boxes, "seeded-static")
        try:
            item.task.world.check_pose(start)
            item.task.world.check_pose(Pose(*goal, 0))
        except Exception:
            continue
        if math.hypot(start.x - goal[0], start.y - goal[1]) < 2 or not _oracle_reachable(item.task.world, start, goal):
            continue
        scenarios.append(item)
        if len(scenarios) == count:
            return tuple(scenarios)
    raise RuntimeError("Could not sample enough valid connected scenes")
