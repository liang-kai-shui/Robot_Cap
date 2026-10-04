from robot.virtual import VirtualRobot
from runtime.errors import PolicyExecutionError


def run_worker(policy, world, initial_pose, limits, output):
    robot = None
    try:
        robot = VirtualRobot(world, initial_pose, limits)
        safe_globals = {"__builtins__": {"range": range, "min": min, "max": max, "abs": abs}, "robot": robot}
        exec(compile(policy, "<policy>", "exec"), safe_globals, {})
        output.send((True, None, None, robot.snapshot(), robot.logs))
    except BaseException as exc:
        state = robot.snapshot() if robot else None
        logs = robot.logs if robot else []
        output.send((False, type(exc).__name__, str(exc), state, logs))
    finally:
        output.close()
