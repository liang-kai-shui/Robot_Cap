"""Trace-based grading for the separate complex known-map benchmark."""
import math
from task.complex_benchmark_tasks import ComplexScenario


def evaluate_complex(scenario: ComplexScenario, result) -> tuple[bool, list[str]]:
    """Grade requested behavior as well as the ordinary final-state goal."""
    criteria = scenario.criteria
    events = result.trace
    moves = [(index, event) for index, event in enumerate(events)
             if event.get("capability_id") == "motion.move" and event.get("phase") == "completed"]
    reads = [index for index, event in enumerate(events)
             if event.get("capability_id") == "sensors.get_distance" and event.get("phase") == "completed"]
    failures = []
    if not result.task_success:
        failures.append("final_goal")
    if len(reads) < criteria.min_distance_reads:
        failures.append("distance_reads")
    if criteria.read_before_first_move and (not moves or not any(index < moves[0][0] for index in reads)):
        failures.append("read_before_move")
    if criteria.read_before_each_move and (not moves or any(
        not any(previous < read < index for read in reads)
        for previous, (index, _) in zip([-1, *(item[0] for item in moves[:-1])], moves)
    )):
        failures.append("read_before_each_move")
    if criteria.read_after_move and (not moves or not any(index > moves[0][0] for index in reads)):
        failures.append("read_after_move")
    if criteria.max_actions is not None and result.final_state.action_count > criteria.max_actions:
        failures.append("action_budget")
    commanded = sum(abs(event["request"].get("distance", 0)) for _, event in moves)
    if criteria.max_commanded_distance is not None and commanded > criteria.max_commanded_distance + 1e-8:
        failures.append("distance_budget")
    if criteria.max_step_distance is not None and any(
        abs(event["request"].get("distance", 0)) > criteria.max_step_distance + 1e-8
        for _, event in moves
    ):
        failures.append("step_distance")
    if criteria.move_speed_range and (not moves or any(
        event["request"].get("speed") is None
        or not criteria.move_speed_range[0] <= event["request"]["speed"] <= criteria.move_speed_range[1]
        for _, event in moves
    )):
        failures.append("speed_schedule")
    if criteria.speed_segments:
        traveled = 0.0
        valid = bool(moves)
        for _, event in moves:
            distance = event["request"].get("distance", 0)
            speed = event["request"].get("speed")
            end = traveled + distance
            segment_start = 0.0
            matching = False
            for segment_end, low, high in criteria.speed_segments:
                if traveled >= segment_start - 1e-8 and end <= segment_end + 1e-8:
                    matching = speed is not None and low <= speed <= high and distance > 0
                    break
                segment_start = segment_end
            valid = valid and matching
            traveled = end
        if not valid:
            failures.append("speed_schedule")
    position = 0
    for _, event in moves:
        if position >= len(criteria.waypoints):
            break
        pose = (event.get("state") or {}).get("pose") or {}
        x, y = criteria.waypoints[position]
        if "x" in pose and "y" in pose and math.hypot(pose["x"] - x, pose["y"] - y) <= .08:
            position += 1
    if position != len(criteria.waypoints):
        failures.append("waypoint_order")
    return not failures, failures
