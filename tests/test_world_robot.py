import math
import pytest
from robot.state import Pose
from robot.virtual import VirtualRobot
from runtime.errors import CollisionError, WorldBoundaryError, MoveLimitExceeded, MoveBelowResolutionError, TurnLimitExceeded
from runtime.limits import RuntimeLimits
from world.geometry import ray_box_distance
from world.obstacle import Obstacle
from world.world import VirtualWorld


def test_motion_turn_and_state():
    robot = VirtualRobot(VirtualWorld(), Pose(1, 1, 0))
    robot.move(1)
    robot.move(-0.5)
    robot.turn(90)
    robot.move(1)
    robot.stop()
    assert robot.get_pose() == Pose(1.5, 2, 90)
    assert robot.get_state().action_count == 5
    assert robot.get_state().stopped
    assert len(robot.logs) >= 5


def test_boundary_and_segment_collision():
    robot = VirtualRobot(VirtualWorld(), Pose(9.5, 1, 0))
    with pytest.raises(WorldBoundaryError): robot.move(1)
    world = VirtualWorld(obstacles=(Obstacle(2, .5, .5, 1),))
    robot = VirtualRobot(world, Pose(1, 1, 0))
    with pytest.raises(CollisionError): robot.move(2)
    assert robot.get_state().collision
    assert robot.get_pose().x == 1


def test_distance_and_limits():
    world = VirtualWorld(obstacles=(Obstacle(3, .5, 1, 1),))
    robot = VirtualRobot(world, Pose(1, 1, 0), RuntimeLimits(max_move_distance=1, max_turn_angle=90))
    assert robot.get_distance() == pytest.approx(2)
    with pytest.raises(MoveLimitExceeded): robot.move(2)
    with pytest.raises(TurnLimitExceeded): robot.turn(180)
    assert robot.logs[-2]["error"] == "MoveLimitExceeded"
    assert robot.logs[-1]["error"] == "TurnLimitExceeded"
    robot.turn(90)
    assert robot.get_distance() == pytest.approx(9)
    assert ray_box_distance(1, 1, 1, 0, world.obstacles[0]) == pytest.approx(2)


def test_distance_rounds_float_boundary():
    world = VirtualWorld(obstacles=(Obstacle(4, .5, .5, 1),))
    robot = VirtualRobot(world, Pose(3.4, 1, 0))
    assert 4 - 3.4 > 0.6  # The raw binary-float artifact that caused repeated tiny moves.
    assert world.distance_ahead(robot.snapshot().pose) == 0.6
    assert robot.get_distance() == 0.6


def test_sub_resolution_move_is_rejected_but_normal_small_move_works():
    robot = VirtualRobot(VirtualWorld(), Pose(1, 1, 0))
    with pytest.raises(MoveBelowResolutionError):
        robot.move(1e-16)
    assert robot.snapshot().pose == Pose(1, 1, 0)
    assert robot.snapshot().action_count == 0
    assert robot.logs[-1]["error"] == "MoveBelowResolutionError"
    robot.move(0.01)
    assert robot.snapshot().pose.x == pytest.approx(1.01)
    assert robot.snapshot().action_count == 1
    robot.move(0.0)  # An explicit zero move remains a logged no-op action.
    assert robot.snapshot().pose.x == pytest.approx(1.01)
    assert robot.snapshot().action_count == 2
