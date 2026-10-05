"""Manifest-limited worker facade for arbitrary registered public API paths."""
import json
from types import SimpleNamespace
from runtime import errors


def _convert_result(value, fields):
    if fields is None or not isinstance(value, dict):
        return value
    return SimpleNamespace(**{key: _convert_result(value[key], nested)
                              for key, nested in fields.items() if key in value})


class CapabilityProxyNode:
    """One manifest-checked prefix or callable public capability path."""

    def __init__(self, robot: "RobotProxy", path: tuple[str, ...]):
        self._robot = robot
        self._path = path

    def __getattr__(self, name: str):
        path = self._path + (name,)
        if name.startswith("_") or not self._robot._known_prefix(path):
            raise AttributeError("Unregistered robot capability path")
        return CapabilityProxyNode(self._robot, path)

    def __call__(self, *args, **kwargs):
        entry = self._robot._paths.get(".".join(self._path))
        if entry is None:
            raise errors.RobotActionError("Capability path is not callable")
        return self._robot._call(entry, args, kwargs)


class RobotProxy(CapabilityProxyNode):
    """Resolve only paths present in the worker's data-only capability manifest."""

    def __init__(self, connection, manifest: dict, communication_timeout_seconds: float):
        self._connection = connection
        self._paths = dict(manifest["paths"])
        self._timeout = communication_timeout_seconds
        self._next_id = 1
        super().__init__(self, ())

    def _known_prefix(self, path: tuple[str, ...]) -> bool:
        dotted = ".".join(path)
        return dotted in self._paths or any(item.startswith(dotted + ".") for item in self._paths)

    def _receive(self, timeout: float) -> dict:
        try:
            if not self._connection.poll(timeout):
                raise errors.RobotCommunicationError("Robot IPC response timed out")
            return json.loads(self._connection.recv_bytes().decode("utf-8"))
        except (OSError, EOFError, ValueError) as exc:
            raise errors.RobotCommunicationError(str(exc)) from exc

    def _call(self, entry: dict, args: tuple, kwargs: dict):
        command_id = self._next_id
        self._next_id += 1
        request = {"kind": "command", "command_id": command_id,
                   "capability_id": entry["capability_id"], "args": args, "kwargs": kwargs}
        try:
            self._connection.send_bytes(json.dumps(request).encode("utf-8"))
        except (OSError, EOFError, ValueError) as exc:
            raise errors.RobotCommunicationError(str(exc)) from exc
        response = self._receive(self._timeout)
        if response.get("command_id") != command_id:
            raise errors.RobotCommunicationError("Mismatched command ID")
        if response.get("kind") == "accepted":
            # Communication timeout covers acknowledgement, not a legitimate action.
            wait = response["action_timeout_seconds"] + self._timeout + 0.2
            response = self._receive(wait)
        if response.get("kind") != "result" or response.get("command_id") != command_id:
            raise errors.RobotCommunicationError("Invalid robot response")
        if not response.get("ok"):
            error_type = response.get("error_type", "RobotError")
            error_class = getattr(errors, error_type, errors.RobotError)
            raise error_class(response.get("error_message", "Robot command failed"))
        return _convert_result(response.get("result"), entry.get("return_fields"))
