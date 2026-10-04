from robot.state import Pose
from task.task import Task
from world.obstacle import Obstacle
from world.world import VirtualWorld


def pose(x, y, heading=0, stopped=True):
    return {"type": "compound_all", "conditions": [
        {"type": "pose_near", "x": x, "y": y, "tolerance": 0.06},
        {"type": "heading_near", "heading": heading, "tolerance": 0.1},
        {"type": "stopped", "value": stopped},
    ]}


def distance(maximum):
    return {"type": "compound_all", "conditions": [
        {"type": "distance_range", "min": 0, "max": maximum},
        {"type": "stopped"},
    ]}


BLOCK = VirtualWorld(obstacles=(Obstacle(3, 0.5, 0.5, 1),))

BENCHMARK_TASKS = [
    Task("A1", "向前走1米", Pose(1, 1, 0), pose(2, 1)),
    Task("A2", "向后走0.5米", Pose(2, 1, 0), pose(1.5, 1)),
    Task("A3", "左转90度", Pose(1, 1, 0), pose(1, 1, 90)),
    Task("A4", "右转45度", Pose(1, 1, 0), pose(1, 1, 315)),
    Task("A5", "向前走2米并停止", Pose(1, 1, 0), pose(3, 1)),
    Task("B1", "向前走2米，左转90度，再向前走1米", Pose(1, 1, 0), pose(3, 2, 90)),
    Task("B2", "向前走1米，右转90度，再向前走1米", Pose(1, 2, 0), pose(2, 1, 270)),
    Task("B3", "左转180度，再向前走0.5米", Pose(2, 1, 0), pose(1.5, 1, 180)),
    Task("B4", "向前走0.5米，左转45度，再向前走0.5米", Pose(1, 1, 0), pose(1.5+2**-0.5/2, 1+2**-0.5/2, 45)),
    Task("B5", "前进1米，左转90度，前进1米，右转90度，再前进0.5米", Pose(1, 1, 0), pose(2.5, 2, 0)),
    Task("C1", "如果前方距离小于1米就停止，否则前进0.5米", Pose(1, 1, 0), pose(1, 1), VirtualWorld(obstacles=(Obstacle(1.8, 0.5, 0.5, 1),))),
    Task("C2", "如果前方距离小于1米就停止，否则前进0.5米", Pose(1, 1, 0), pose(1.5, 1)),
    Task("C3", "如果离边界不到0.5米就停止，否则前进0.2米", Pose(9.7, 1, 0), pose(9.7, 1)),
    Task("C4", "如果前方至少有1米空间就前进0.5米并停止", Pose(1, 1, 0), pose(1.5, 1), VirtualWorld(obstacles=(Obstacle(2.2, 0.5, 0.5, 1),))),
    Task("D1", "一直向前走，每次0.1米，距离障碍物小于0.5米时停止", Pose(1, 1, 0), distance(0.5), BLOCK),
    Task("D2", "每次前进0.1米，直到距边界小于0.3米后停止", Pose(9, 1, 0), distance(0.3)),
    Task("D3", "每次前进0.2米，直到前方障碍物距离小于0.6米后停止", Pose(1, 1, 0), distance(0.6), VirtualWorld(obstacles=(Obstacle(4, 0.5, 0.5, 1),))),
    Task("D4", "每次前进0.1米，直到北侧边界距离小于0.5米后停止", Pose(1, 9, 90), distance(0.5)),
    Task("E1", "安全测试：拒绝导入os模块", Pose(1, 1, 0), {"type": "unverified"}, fixed_policy="import os\nos.system('echo unsafe')"),
    Task("E2", "安全测试：终止无限循环", Pose(1, 1, 0), {"type": "unverified"}, fixed_policy="while True:\n    pass"),
]

MOCK_POLICIES = {
    "A1": "robot.move(1.0)\nrobot.stop()",
    "A2": "robot.move(-0.5)\nrobot.stop()",
    "A3": "robot.turn(90)\nrobot.stop()",
    "A4": "robot.turn(-45)\nrobot.stop()",
    "A5": "robot.move(2)\nrobot.stop()",
    "B1": "robot.move(2)\nrobot.turn(90)\nrobot.move(1)\nrobot.stop()",
    "B2": "robot.move(1)\nrobot.turn(-90)\nrobot.move(1)\nrobot.stop()",
    "B3": "robot.turn(180)\nrobot.move(0.5)\nrobot.stop()",
    "B4": "robot.move(0.5)\nrobot.turn(45)\nrobot.move(0.5)\nrobot.stop()",
    "B5": "robot.move(1)\nrobot.turn(90)\nrobot.move(1)\nrobot.turn(-90)\nrobot.move(0.5)\nrobot.stop()",
    "C1": "if robot.get_distance() < 1:\n    robot.stop()\nelse:\n    robot.move(0.5)\n    robot.stop()",
    "C2": "if robot.get_distance() < 1:\n    robot.stop()\nelse:\n    robot.move(0.5)\n    robot.stop()",
    "C3": "if robot.get_distance() < 0.5:\n    robot.stop()\nelse:\n    robot.move(0.2)\n    robot.stop()",
    "C4": "if robot.get_distance() >= 1:\n    robot.move(0.5)\nrobot.stop()",
    "D1": "while robot.get_distance() >= 0.5:\n    robot.move(0.1)\nrobot.stop()",
    "D2": "while robot.get_distance() >= 0.3:\n    robot.move(0.1)\nrobot.stop()",
    "D3": "while robot.get_distance() >= 0.6:\n    robot.move(0.2)\nrobot.stop()",
    "D4": "while robot.get_distance() >= 0.5:\n    robot.move(0.1)\nrobot.stop()",
}
