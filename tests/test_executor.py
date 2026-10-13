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


def test_feedback_threshold_does_not_repeat_sub_resolution_moves():
    world = VirtualWorld(obstacles=(Obstacle(4, .5, .5, 1),))
    policy = """while robot.get_distance() >= 0.6:
    d = robot.get_distance()
    step = min(0.2, d - 0.6)
    if step <= 0:
        break
    robot.move(step)
robot.stop()"""
    result = PolicyExecutor(RuntimeLimits(timeout_seconds=2, max_actions=100)).execute(policy, world, Pose(1, 1, 0))
    assert result.success
    assert result.final_state.action_count < 20
    assert world.distance_ahead(result.final_state.pose) == 0.6
    assert result.final_state.stopped


def test_repeated_sub_resolution_move_stops_on_first_call():
    result = PolicyExecutor(RuntimeLimits(timeout_seconds=2)).execute(
        "while True:\n    robot.move(1e-16)", VirtualWorld(), Pose(1, 1, 0))
    assert result.error_type == "MoveBelowResolutionError"
    assert result.final_state.action_count == 0
    assert len(result.logs) == 1


# Spawnable workers reproduce exit cleanup delays independently of host speed.
def delayed_exit_worker(*args):
    import time
    from runtime.worker import run_worker
    run_worker(*args)
    time.sleep(.2)


def stuck_exit_worker(*args):
    import time
    from runtime.worker import run_worker
    run_worker(*args)
    while True:
        time.sleep(1)


def abnormal_exit_worker(*args):
    import os
    from runtime.worker import run_worker
    run_worker(*args)
    os._exit(7)


def execute_demo_with_worker(monkeypatch, worker):
    from webui.demo import DemoBackend
    from robot.backends.esp32_serial import build_esp32_registry
    from robot.runtime import RobotRuntime
    monkeypatch.setattr('runtime.executor.run_worker', worker)
    backend = DemoBackend().connect()
    runtime = RobotRuntime(backend, registry=build_esp32_registry(backend))
    outcome = PolicyExecutor().execute('robot.stop()', None, Pose(0, 0, 0), runtime=runtime)
    return backend, outcome


def test_completed_worker_slow_exit_does_not_poison_stopped_device(monkeypatch):
    backend, outcome = execute_demo_with_worker(monkeypatch, delayed_exit_worker)
    assert outcome.success
    assert backend.snapshot()['ready'] and backend.snapshot()['stopped']
    assert backend.fault is None
    assert not any(event['capability_id'] == 'system.emergency_stop' for event in outcome.trace)


def test_reported_success_with_stuck_worker_is_failure_and_emergency_stop(monkeypatch):
    backend, outcome = execute_demo_with_worker(monkeypatch, stuck_exit_worker)
    assert not outcome.success
    assert outcome.error_type == 'PolicyExecutionError'
    assert backend.fault == 'SESSION_STOPPED' and backend.stopped
    assert any(event['capability_id'] == 'system.emergency_stop' for event in outcome.trace)


def test_reported_success_with_abnormal_exit_is_failure_and_emergency_stop(monkeypatch):
    backend, outcome = execute_demo_with_worker(monkeypatch, abnormal_exit_worker)
    assert not outcome.success
    assert outcome.error_type == 'PolicyExecutionError'
    assert backend.fault == 'SESSION_STOPPED' and backend.stopped
