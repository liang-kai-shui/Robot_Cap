"""Worker-side RPC facade; no backend or world is imported here."""
import json
from robot.state import Pose, RobotState
from runtime import errors


class RobotProxy:
    """Legacy robot API that forwards calls over a JSON pipe."""

    def __init__(self, connection, communication_timeout_seconds: float):
        self._connection = connection
        self._timeout = communication_timeout_seconds
        self._next_id = 1

    def _call(self, action: str, *args, **kwargs):
        command_id = self._next_id
        self._next_id += 1
        request = {"kind": "command", "command_id": command_id, "action": action,
                   "args": args, "kwargs": kwargs}
        try:
            self._connection.send_bytes(json.dumps(request).encode("utf-8"))
            if not self._connection.poll(self._timeout):
                raise errors.RobotCommunicationError("Robot response timed out")
            response = json.loads(self._connection.recv_bytes().decode("utf-8"))
        except (OSError, EOFError, ValueError) as exc:
            raise errors.RobotCommunicationError(str(exc)) from exc
        if response.get("command_id") != command_id:
            raise errors.RobotCommunicationError("Mismatched command ID")
        if not response.get("ok"):
            error_type = response.get("error_type", "RobotError")
            error_class = getattr(errors, error_type, errors.RobotError)
            raise error_class(response.get("error_message", "Robot command failed"))
        return response.get("result")

    def move(self, distance: float, speed: float | None = None) -> None:
        """Complete a finite linear move."""
        self._call("move", distance, speed=speed)

    def turn(self, angle: float, speed: float | None = None) -> None:
        """Complete a finite turn."""
        self._call("turn", angle, speed=speed)

    def stop(self) -> None:
        """Stop the robot."""
        self._call("stop")

    def get_distance(self) -> float:
        """Read distance ahead."""
        return self._call("get_distance")

    def get_pose(self) -> Pose:
        """Read current pose."""
        return Pose(**self._call("get_pose"))

    def get_state(self) -> RobotState:
        """Read current state."""
        state = self._call("get_state")
        state["pose"] = Pose(**state["pose"])
        return RobotState(**state)
