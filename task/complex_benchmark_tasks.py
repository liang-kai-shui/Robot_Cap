"""Independent known-map challenges; reference policies are never sent to an LLM."""
from dataclasses import dataclass
from robot.state import Pose
from task.task import Task
from world.obstacle import Obstacle
from world.world import VirtualWorld


@dataclass(frozen=True)
class ComplexCriteria:
    waypoints: tuple[tuple[float, float], ...] = ()
    min_distance_reads: int = 0
    read_before_first_move: bool = False
    read_before_each_move: bool = False
    read_after_move: bool = False
    max_actions: int | None = None
    max_commanded_distance: float | None = None
    max_step_distance: float | None = None
    move_speed_range: tuple[float, float] | None = None
    speed_segments: tuple[tuple[float, float, float], ...] = ()


@dataclass(frozen=True)
class ComplexScenario:
    task: Task
    category: str
    criteria: ComplexCriteria
    reference_policy: str


def target(x, y, heading=0, tolerance=0.08):
    return {"type": "compound_all", "conditions": [
        {"type": "pose_near", "x": x, "y": y, "tolerance": tolerance},
        {"type": "heading_near", "heading": heading, "tolerance": 1},
        {"type": "stopped"},
    ]}


def approach(low, high):
    return {"type": "compound_all", "conditions": [
        {"type": "distance_range", "min": low, "max": high},
        {"type": "stopped"},
    ]}


GATES = VirtualWorld(obstacles=(
    Obstacle(3, 0, .4, 3.5), Obstacle(3, 4.5, .4, 5.5),
    Obstacle(6, 0, .4, 5.5), Obstacle(6, 6.5, .4, 3.5),
), clearance=.08)
DETOUR = VirtualWorld(obstacles=(Obstacle(3, 2, 2, 2), Obstacle(7, 3.5, 1, 2)), clearance=.1)
CORRIDOR = VirtualWorld(obstacles=(Obstacle(1, 2, 6, .7), Obstacle(1, 3.3, 6, .7)), clearance=.1)
APPROACH = VirtualWorld(obstacles=(Obstacle(4, 0, .5, 3), Obstacle(7, 5, .5, 3)), clearance=.1)
SENSING = VirtualWorld(obstacles=(Obstacle(3, 1.5, .5, 1), Obstacle(6, 4, .5, 2)), clearance=.1)
CHOICE = VirtualWorld(obstacles=(Obstacle(3, 4, .5, 2), Obstacle(6, 6, .5, 2)), clearance=.1)
DELIVERY = VirtualWorld(obstacles=(Obstacle(2, 1, 1, 2.2), Obstacle(5, 5, 1, 2)), clearance=.1)


COMPLEX_SCENARIOS = (
    ComplexScenario(Task("N1", "已知地图有两道错位门。依次穿过西门和东门，到达(8,6)，朝东并停稳。", Pose(1, 4, 0), target(8, 6), GATES),
                    "navigation", ComplexCriteria(waypoints=((4, 4), (5.5, 6), (7, 6)), max_actions=10, max_commanded_distance=9.1),
                    "robot.move(1.5)\nrobot.move(1.5)\nrobot.move(1.5)\nrobot.turn(90)\nrobot.move(2)\nrobot.turn(-90)\nrobot.move(1.5)\nrobot.move(1)"),
    ComplexScenario(Task("N2", "从东侧反向穿过两道错位门，到达(1,4)，朝西并停稳。", Pose(8, 6, 180), target(1, 4, 180), GATES),
                    "navigation", ComplexCriteria(waypoints=((5.5, 6), (5.5, 4), (4, 4)), max_actions=10, max_commanded_distance=9.1),
                    "robot.move(2)\nrobot.move(0.5)\nrobot.turn(90)\nrobot.move(2)\nrobot.turn(-90)\nrobot.move(1.5)\nrobot.move(1.5)\nrobot.move(1.5)"),
    ComplexScenario(Task("N3", "绕过中央障碍物上方，先到(2,4.5)，再到(6,4.5)，最后到(6,3)，朝南停稳。", Pose(2, 3, 0), target(6, 3, 270), DETOUR),
                    "navigation", ComplexCriteria(waypoints=((2, 4.5), (6, 4.5)), max_actions=8, max_commanded_distance=7),
                    "robot.turn(90)\nrobot.move(1.5)\nrobot.turn(-90)\nrobot.move(2)\nrobot.move(2)\nrobot.turn(-90)\nrobot.move(1.5)"),
    ComplexScenario(Task("N4", "沿宽度有限的直走廊前进到(7.5,3)，朝东停稳；不得触碰两侧障碍。", Pose(1.2, 3, 0), target(7.5, 3), CORRIDOR),
                    "navigation", ComplexCriteria(max_actions=5, max_commanded_distance=6.5),
                    "robot.move(2)\nrobot.move(2)\nrobot.move(2)\nrobot.move(0.3)"),
    ComplexScenario(Task("F1", "反复测量前方距离，每次最多前进0.3米，直到距障碍约0.5米后停稳。", Pose(1, 1, 0), approach(.4, .7), APPROACH),
                    "feedback", ComplexCriteria(min_distance_reads=5, read_before_first_move=True, read_before_each_move=True,
                                                 read_after_move=True, max_step_distance=.3,
                                                 max_actions=10, max_commanded_distance=2.7),
                    "while robot.get_distance() > 0.7:\n    robot.move(0.3)"),
    ComplexScenario(Task("F2", "朝北反复测距，每次前进0.4米，接近北侧边界到0.4至0.6米后停稳。", Pose(2, 7, 90), approach(.4, .6)),
                    "feedback", ComplexCriteria(min_distance_reads=5, read_before_first_move=True, read_before_each_move=True,
                                                 read_after_move=True, max_step_distance=.4,
                                                 max_actions=7, max_commanded_distance=2.8),
                    "while robot.get_distance() > 0.6:\n    robot.move(0.4)"),
    ComplexScenario(Task("AS1", "先测距确认东侧障碍，再绕其北侧；途中再次测距，最终到(4,3.5)朝东停稳。", Pose(1, 2, 0), target(4, 3.5), SENSING),
                    "active_sensing", ComplexCriteria(waypoints=((1, 3.5),), min_distance_reads=2,
                                                       read_before_first_move=True, read_after_move=True,
                                                       max_actions=6, max_commanded_distance=5),
                    "front = robot.get_distance()\nif front < 3:\n    robot.turn(90)\n    robot.move(1.5)\n    robot.get_distance()\n    robot.turn(-90)\n    robot.get_distance()\n    robot.move(1.5)\n    robot.move(1.5)"),
    ComplexScenario(Task("AS2", "先测距选择绕开西侧墙的下方路线；行进途中重新测距，到(5,3.5)朝东停稳。", Pose(1, 5, 0), target(5, 3.5), CHOICE),
                    "active_sensing", ComplexCriteria(waypoints=((1, 3.5),), min_distance_reads=3,
                                                       read_before_first_move=True, read_after_move=True,
                                                       max_actions=6, max_commanded_distance=6),
                    "front = robot.get_distance()\nif front < 2.5:\n    robot.turn(-90)\n    robot.get_distance()\n    robot.move(1.5)\n    robot.get_distance()\n    robot.turn(90)\n    robot.get_distance()\n    robot.move(2)\n    robot.move(2)"),
    ComplexScenario(Task("SP1", "在开阔区域走3米到(4,1)，每次移动明确指定不超过0.2米每秒的速度，朝东停稳。", Pose(1, 1, 0), target(4, 1)),
                    "speed", ComplexCriteria(max_actions=3, max_commanded_distance=3.1,
                                             move_speed_range=(.01, .2)),
                    "robot.move(1.5, speed=0.15)\nrobot.move(1.5, speed=0.15)"),
    ComplexScenario(Task("SP2", "前1米以0.1米每秒慢行，随后2米以0.3米每秒行进，到(4,2)朝东停稳。", Pose(1, 2, 0), target(4, 2)),
                    "speed", ComplexCriteria(max_actions=5, max_commanded_distance=3.1,
                                             speed_segments=((1, .09, .11), (3, .29, .31))),
                    "robot.move(1, speed=0.1)\nrobot.move(2, speed=0.3)"),
    ComplexScenario(Task("MX1", "穿过两道错位门到(7,6)，沿途主动测距，移动速度不超过0.2米每秒，朝东停稳。", Pose(1, 4, 0), target(7, 6), GATES),
                    "mixed", ComplexCriteria(waypoints=((4, 4), (5.5, 6)), min_distance_reads=2,
                                             read_before_first_move=True, read_after_move=True,
                                             max_actions=8, max_commanded_distance=8.1,
                                             move_speed_range=(.01, .2)),
                    "robot.get_distance()\nrobot.move(1.5, speed=0.2)\nrobot.move(1.5, speed=0.2)\nrobot.get_distance()\nrobot.move(1.5, speed=0.2)\nrobot.turn(90)\nrobot.get_distance()\nrobot.move(2, speed=0.2)\nrobot.turn(-90)\nrobot.move(1.5, speed=0.2)"),
    ComplexScenario(Task("MX2", "先到(1,4)，再到(4,4)，最后到(4,5)朝北停稳；途中测距且移动速度不超过0.25米每秒。", Pose(1, 1, 90), target(4, 5, 90), DELIVERY),
                    "mixed", ComplexCriteria(waypoints=((1, 4), (4, 4)), min_distance_reads=2,
                                             read_before_first_move=True, read_after_move=True,
                                             max_actions=8, max_commanded_distance=7.2,
                                             move_speed_range=(.01, .25)),
                    "robot.get_distance()\nrobot.move(2, speed=0.2)\nrobot.move(1, speed=0.2)\nrobot.get_distance()\nrobot.turn(-90)\nrobot.move(2, speed=0.2)\nrobot.move(1, speed=0.2)\nrobot.turn(90)\nrobot.get_distance()\nrobot.move(1, speed=0.2)"),
)
