"""A small local observation for software-only interactive planning."""
from dataclasses import asdict
import time
from robot.runtime import RobotRuntime


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
            raise RuntimeError(response["error_message"])
        # The demo sensor exposes only a nearby range, even when the virtual ray travels farther.
        observation["front_distance_m"] = min(2.0, response["result"])
        observation["front_range_limit_m"] = 2.0
    return observation
