"""Runtime-registered capabilities and the current robot's default API."""
from dataclasses import dataclass
from typing import Any, Callable


API_VERSION = "v1"
CapabilityHandler = Callable[..., Any]
ArgumentValidator = Callable[[dict, Any], dict]


@dataclass(frozen=True)
class ParameterSpec:
    """One positional or named argument in a public capability call."""

    name: str
    required: bool = True


@dataclass(frozen=True)
class CapabilitySpec:
    """Stable capability identity, public aliases, and callable schema."""

    canonical_id: str
    version: str
    public_paths: tuple[tuple[str, ...], ...]
    arguments: tuple[ParameterSpec, ...] = ()
    return_fields: dict[str, Any] | None = None
    blocking: bool = False
    observation: bool = False
    description: str = ""
    timeout_seconds: float | None = None

    @property
    def signature(self) -> str:
        """Return this capability's versioned identity."""
        return f"{self.canonical_id}@{self.version}"

    def bind(self, args: tuple, kwargs: dict) -> dict:
        """Validate argument names and arity without inspecting values."""
        names = [item.name for item in self.arguments]
        if len(args) > len(names):
            raise TypeError(f"{self.canonical_id} accepts at most {len(names)} arguments")
        values = dict(zip(names, args))
        for name, value in kwargs.items():
            if name not in names or name in values:
                raise TypeError(f"Invalid or duplicate {self.canonical_id} argument: {name}")
            values[name] = value
        if any(item.required and item.name not in values for item in self.arguments):
            raise TypeError(f"Missing required {self.canonical_id} argument")
        return values


@dataclass
class RegisteredCapability:
    """A specification bound to a trusted handler and optional safety validator."""

    spec: CapabilitySpec
    handler: CapabilityHandler
    validator: ArgumentValidator | None = None
    emergency_stop: Callable[[], None] | None = None


class CapabilityRegistry:
    """Registry with no built-in knowledge of hardware types or namespaces."""

    def __init__(self):
        self._items: dict[str, RegisteredCapability] = {}
        self._paths: dict[tuple[str, ...], str] = {}

    def register(self, spec: CapabilitySpec, handler: CapabilityHandler,
                 validator: ArgumentValidator | None = None,
                 emergency_stop: Callable[[], None] | None = None) -> None:
        """Register a handler, rejecting identity and public-path collisions."""
        if spec.canonical_id in self._items:
            raise ValueError(f"Duplicate capability: {spec.canonical_id}")
        if not spec.public_paths or not callable(handler):
            raise ValueError("A capability needs a public path and callable handler")
        if spec.timeout_seconds is not None and spec.timeout_seconds <= 0:
            raise ValueError("Capability timeout must be positive")
        paths = tuple(tuple(path) for path in spec.public_paths)
        if len(paths) != len(set(paths)) or any(not path or any(not part.isidentifier() or part.startswith("_") for part in path) for path in paths):
            raise ValueError("Invalid or duplicate public path")
        if any(first != second and first == second[:len(first)] for first in paths for second in paths):
            raise ValueError("Public paths in one specification cannot shadow each other")
        for path in paths:
            if path in self._paths or any(path[:i] in self._paths for i in range(1, len(path))):
                raise ValueError(f"Ambiguous public path: {'.'.join(path)}")
            if any(existing[:len(path)] == path for existing in self._paths):
                raise ValueError(f"Public path shadows a registered path: {'.'.join(path)}")
        self._items[spec.canonical_id] = RegisteredCapability(spec, handler, validator, emergency_stop)
        self._paths.update({path: spec.canonical_id for path in paths})

    def unregister(self, canonical_id: str) -> None:
        """Remove a capability and all of its public aliases."""
        del self._items[canonical_id]
        self._paths = {path: identity for path, identity in self._paths.items() if identity != canonical_id}

    def get(self, canonical_id: str) -> RegisteredCapability | None:
        """Look up a registered canonical identity."""
        return self._items.get(canonical_id)

    def resolve_public_path(self, path: tuple[str, ...]) -> RegisteredCapability | None:
        """Resolve an exact public path; prefixes are not callable."""
        identity = self._paths.get(tuple(path))
        return self._items.get(identity) if identity else None

    def list_visible(self) -> tuple[CapabilitySpec, ...]:
        """List only registered capabilities visible to policies."""
        return tuple(item.spec for item in self._items.values())

    def api_surface_signature(self) -> list[str]:
        """Return the full registered API surface for episode compatibility."""
        return [spec.signature for spec in self.list_visible()]

    def manifest(self) -> dict:
        """Return a data-only worker manifest without trusted handlers."""
        return {"paths": {".".join(path): {"capability_id": identity,
                                           "return_fields": self._items[identity].spec.return_fields}
                          for path, identity in self._paths.items()}}

    def emergency_stops(self) -> tuple[Callable[[], None], ...]:
        """Return distinct registered component stop callbacks."""
        return tuple(dict.fromkeys(item.emergency_stop for item in self._items.values()
                                   if item.emergency_stop is not None))


POSE_FIELDS = {"x": None, "y": None, "heading": None}
STATE_FIELDS = {"pose": POSE_FIELDS, "stopped": None, "collision": None,
                "last_action": None, "action_count": None}


def _unbound_handler(**_kwargs):
    raise RuntimeError("Default schema registry has no bound backend")


def build_default_registry(backend=None) -> CapabilityRegistry:
    """Register only the V1 capabilities implemented by the supplied backend."""
    registry = CapabilityRegistry()
    specs = (
        CapabilitySpec("motion.move", API_VERSION, (("move",),),
                       (ParameterSpec("distance"), ParameterSpec("speed", False)),
                       blocking=True, description="finite blocking move, returns stopped; distance in m (+ forward, - backward), max 2m; optional speed in m/s"),
        CapabilitySpec("motion.turn", API_VERSION, (("turn",),),
                       (ParameterSpec("angle"), ParameterSpec("speed", False)),
                       blocking=True, description="finite blocking turn, returns stopped; angle in degrees (+ counterclockwise), max 180; optional speed in deg/s"),
        CapabilitySpec("motion.stop", API_VERSION, (("stop",),),
                       description="stop immediately when needed"),
        CapabilitySpec("sensors.get_distance", API_VERSION, (("get_distance",),),
                       observation=True, description="distance ahead to obstacle or boundary in m"),
        CapabilitySpec("state.get_pose", API_VERSION, (("get_pose",),),
                       return_fields=POSE_FIELDS, observation=True, description="Pose with x, y, heading"),
        CapabilitySpec("state.get_state", API_VERSION, (("get_state",),),
                       return_fields=STATE_FIELDS, observation=True,
                       description="state with pose, stopped, collision, last_action, action_count"),
    )
    from robot.motion_validation import validate_move, validate_turn
    validators = {"motion.move": validate_move, "motion.turn": validate_turn}
    for spec in specs:
        name = spec.canonical_id.rsplit(".", 1)[-1]
        handler = getattr(backend, name) if backend is not None else _unbound_handler
        registry.register(spec, handler, validators.get(spec.canonical_id),
                          backend.emergency_stop if backend is not None else None)
    return registry


DEFAULT_REGISTRY = build_default_registry()
