"""Versioned geometric passage and stopped-arrival metrics; old scoring is untouched."""
import math


def _progress(start, end, goal):
    dx, dy = end["x"] - start["x"], end["y"] - start["y"]
    length2 = dx * dx + dy * dy
    fraction = (max(0, min(1, ((goal.x - start["x"]) * dx + (goal.y - start["y"]) * dy) / length2))
                if length2 else 0)
    distance = math.hypot(start["x"] + fraction * dx - goal.x, start["y"] + fraction * dy - goal.y)
    return fraction if distance <= goal.tolerance else None


def evaluate_waypoints(trace, initial_state, goals):
    """Grade only after execution. Goals are private evaluator inputs."""
    passed = stopped = 0
    pose = initial_state["pose"]
    while passed < len(goals) and _progress(pose, pose, goals[passed]) is not None:
        passed += 1
    if initial_state["stopped"]:
        while stopped < len(goals) and _progress(pose, pose, goals[stopped]) is not None:
            stopped += 1
    starts = {}
    for event in trace:
        state = event.get("state") or {}
        current = state.get("pose")
        if current is None:
            continue
        if event["phase"] == "started":
            starts[event["command_id"]] = current
        if event["phase"] != "completed":
            continue
        if event["capability_id"] == "motion.move":
            beginning = starts.get(event["command_id"], current)
            previous_fraction = 0
            while passed < len(goals):
                fraction = _progress(beginning, current, goals[passed])
                if fraction is None or fraction + 1e-9 < previous_fraction:
                    break
                previous_fraction = fraction
                passed += 1
        if event["kind"] in ("action", "safety") and state.get("stopped"):
            while stopped < len(goals) and _progress(current, current, goals[stopped]) is not None:
                stopped += 1
    return {"scorer_version": "waypoints-v2", "waypoints_passed": passed,
            "waypoints_stopped": stopped, "passage_success": passed == len(goals),
            "stopped_order_success": stopped == len(goals)}
