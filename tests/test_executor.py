from robot.state import Pose
from runtime.executor import PolicyExecutor
from runtime.limits import RuntimeLimits
from world.obstacle import Obstacle
from world.world import VirtualWorld


def test_simple_and_action_limit():
    executor = PolicyExecutor(RuntimeLimits(timeout_seconds=2, max_actions=3))
    result = executor.execute("robot.move(1)\nrobot.stop()", VirtualWorld(), Pose(1, 1, 0))
    assert result.success and result.final_state.pose.x == 2
    assert result.final_state.action_count == 2
    limited = executor.execute("while True:\n    robot.move(0.01)", VirtualWorld(), Pose(1, 1, 0))
    assert limited.error_type == "ActionLimitExceeded"
    assert limited.final_state.action_count == 3


def test_timeout_and_main_survives():
    executor = PolicyExecutor(RuntimeLimits(timeout_seconds=.8))
    result = executor.execute("while True:\n    pass", VirtualWorld(), Pose(1, 1, 0))
    assert result.error_type == "PolicyTimeoutError"
    assert not result.success
    next_result = executor.execute("robot.stop()", VirtualWorld(), Pose(1, 1, 0))
    assert next_result.success


def test_collision_result():
    world = VirtualWorld(obstacles=(Obstacle(2, .5, .5, 1),))
    result = PolicyExecutor().execute("robot.move(2)", world, Pose(1, 1, 0))
    assert result.error_type == "CollisionError"
    assert result.final_state.collision
