"""The registered V1 robot API shared by validation, dispatch, and prompts."""
from dataclasses import dataclass


API_VERSION = "v1"


@dataclass(frozen=True)
class Capability:
    """One callable legacy API method and its canonical capability name."""

    namespace: str
    name: str
    arguments: tuple[str, ...] = ()
    required: int = 0
    blocking: bool = False
    observation: bool = False
    description: str = ""

    @property
    def signature(self) -> str:
        """Stable identifier for episode compatibility."""
        return f"{self.namespace}.{self.name}@{API_VERSION}"

    def bind(self, args: tuple, kwargs: dict) -> dict:
        """Validate arity and names and return ordered named arguments."""
        if len(args) > len(self.arguments):
            raise TypeError(f"{self.name} accepts at most {len(self.arguments)} arguments")
        values = dict(zip(self.arguments, args))
        for name, value in kwargs.items():
            if name not in self.arguments or name in values:
                raise TypeError(f"Invalid or duplicate {self.name} argument: {name}")
            values[name] = value
        if any(name not in values for name in self.arguments[:self.required]):
            raise TypeError(f"Missing required {self.name} argument")
        return values


CAPABILITIES = {
    item.name: item for item in (
        Capability("motion", "move", ("distance", "speed"), 1, True,
                   description="distance in m (forward +, backward -); optional speed in m/s"),
        Capability("motion", "turn", ("angle", "speed"), 1, True,
                   description="angle in degrees (counterclockwise +); optional speed in deg/s"),
        Capability("motion", "stop", description="stop motion"),
        Capability("sensors", "get_distance", observation=True,
                   description="distance ahead to obstacle or boundary in m"),
        Capability("state", "get_pose", observation=True,
                   description="Pose with x, y, heading"),
        Capability("state", "get_state", observation=True,
                   description="RobotState with pose, stopped, collision, last_action, action_count"),
    )
}


def capability_signature() -> list[str]:
    """Return the API surface used to interpret a saved episode."""
    return [item.signature for item in CAPABILITIES.values()]
