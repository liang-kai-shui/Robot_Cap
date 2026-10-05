"""Small hidden-map tasks for testing local observation and replanning."""
from robot.state import Pose
from task.task import Task
from world.obstacle import Obstacle
from world.world import VirtualWorld


GOAL = {"type": "compound_all", "conditions": [
    {"type": "pose_near", "x": 4, "y": 1, "tolerance": .08},
    {"type": "stopped"},
]}
INSTRUCTION = "到达(4,1)并停稳。地图未知；依据每轮提供的局部位姿和前向测距避开障碍。"


INTERACTIVE_TASKS = (
    Task("H1", INSTRUCTION, Pose(1, 1, 0), GOAL, VirtualWorld(clearance=.1)),
    Task("H2", INSTRUCTION, Pose(1, 1, 0), GOAL,
         VirtualWorld(obstacles=(Obstacle(2, .6, .5, .8),), clearance=.1)),
    Task("H3", INSTRUCTION, Pose(1, 1, 0), GOAL,
         VirtualWorld(obstacles=(Obstacle(2.7, .6, .5, .8),), clearance=.1)),
    Task("H4", INSTRUCTION, Pose(1, 1, 0), GOAL,
         VirtualWorld(obstacles=(Obstacle(3.2, .6, .5, .8),), clearance=.1)),
)
