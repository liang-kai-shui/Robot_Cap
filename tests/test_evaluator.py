from robot.state import Pose, RobotState
from task.evaluator import TaskEvaluator
from task.task import Task
from world.world import VirtualWorld


def test_all_success_conditions():
    state = RobotState(Pose(3, 1, 90), stopped=True)
    task = Task("t", "", Pose(1, 1, 0), {"type": "compound_all", "conditions": [
        {"type": "pose_near", "x": 3, "y": 1, "tolerance": .05},
        {"type": "heading_near", "heading": 90, "tolerance": 1},
        {"type": "distance_range", "min": 0, "max": 9},
        {"type": "stopped"}]})
    assert TaskEvaluator().evaluate(task, state)
    wrong = Task("wrong", "", task.initial, {"type": "pose_near", "x": 4, "y": 1})
    assert not TaskEvaluator().evaluate(wrong, state)


def test_heading_wrap_and_any():
    state = RobotState(Pose(1, 1, 359))
    task = Task("t", "", state.pose, {"type": "compound_any", "conditions": [
        {"type": "stopped"}, {"type": "heading_near", "heading": 0, "tolerance": 2}]})
    assert TaskEvaluator().evaluate(task, state)
