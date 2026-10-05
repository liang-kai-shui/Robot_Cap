"""A small local observation for software-only interactive planning."""
from dataclasses import asdict
import time
from robot.runtime import RobotRuntime
from navigation.observation import RangeObservation
from runtime import errors


def sample_local_observation(runtime: RobotRuntime) -> dict:
    """Expose current state and a registered forward reading, never the world map."""
    state = runtime.snapshot()
    if state is None:
        raise RuntimeError("Interactive planning needs a state observation")
    observation = {
        "timestamp": time.time(),
        "scope": "local_observation_only",
        "pose_estimate": asdict(state.pose),
        "stopped": state.stopped,
        "action_count": state.action_count,
    }
    if runtime.registry.get("sensors.get_distance") is not None:
        response = runtime.dispatch({"command_id": runtime.next_command_id,
                                     "capability_id": "sensors.get_distance", "args": [], "kwargs": {}})
        if not response["ok"]:
            error_class = getattr(errors, response.get("error_type", "RobotError"), errors.RobotError)
            raise error_class(response["error_message"])
        # Adapt the implemented simulator sensor; raw physical readings need another adapter.
        measurement = RangeObservation.from_reading(response["result"], state.pose, "sensors.get_distance")
        observation["range_observation"] = measurement.to_dict()
        observation["front_distance_m"] = measurement.distance_m
        observation["front_range_limit_m"] = 2.0
    return observation
