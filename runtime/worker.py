"""Untrusted policy process: contains only a proxy, never a backend."""
import json
from robot.proxy import RobotProxy


def run_worker(policy: str, connection, manifest: dict, communication_timeout_seconds: float) -> None:
    """Execute validated policy code and report its outcome to the parent."""
    try:
        robot = RobotProxy(connection, manifest, communication_timeout_seconds)
        connection.send_bytes(json.dumps({"kind": "ready"}).encode("utf-8"))
        safe_globals = {"__builtins__": {"range": range, "min": min, "max": max, "abs": abs}, "robot": robot}
        exec(compile(policy, "<policy>", "exec"), safe_globals, {})
        outcome = {"kind": "outcome", "success": True, "error_type": None, "error_message": None}
    except BaseException as exc:
        outcome = {"kind": "outcome", "success": False,
                   "error_type": type(exc).__name__, "error_message": str(exc)}
    try:
        connection.send_bytes(json.dumps(outcome).encode("utf-8"))
    except (OSError, EOFError):
        pass
    finally:
        connection.close()
