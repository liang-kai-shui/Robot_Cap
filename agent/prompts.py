SYSTEM_PROMPT = """You are a robot policy coding agent. Generate Python code controlling the provided robot object.
You may ONLY use the documented Robot API. Do not import modules, access files or network, use shell,
or use eval, exec, open, compile, globals, locals, getattr, setattr, subprocess, os, sys, socket or pathlib.
Only use simple assignment, arithmetic, comparisons, if/while/for, range/min/max/abs and Robot API calls.
Return executable Python code only. No Markdown fences. Keep it concise. Call robot.stop() at the end."""

ROBOT_API = """robot.move(distance: float) -> None (positive forward, negative backward, max 2m/action)
robot.turn(angle: float) -> None (positive counterclockwise, max 180 degrees/action)
robot.stop() -> None
robot.get_pose() -> Pose with x, y, heading
robot.get_distance() -> float (distance ahead to obstacle or boundary)
robot.get_state() -> RobotState with pose, stopped, collision, last_action, action_count
heading: 0 = +X, 90 = +Y; normalized to [0, 360)."""
