"""Bounded summaries of actual feedback from this console session only."""
import math

from robot.hardware_limits import move_mm, turn_tenths
from runtime.errors import RobotActionError


HISTORY_TASK_LIMIT = 4


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _completed_actions(task):
    actions, valid = [], True
    for event in task.get("trace", []):
        if event.get("kind") != "action" or event.get("phase") != "completed":
            continue
        capability = event.get("capability_id")
        request, result = event.get("request") or {}, event.get("result") or {}
        try:
            if capability in ("motion.move","motion.reverse"):
                distance = request.get("distance")
                mm = move_mm(distance)
                if (result.get("encoder_valid") is not True or result.get("yaw_valid") is not True or
                        not _number(result.get("target_mm")) or abs(result["target_mm"]-mm) > .05):
                    raise ValueError("Invalid move feedback")
                action = {"name": "reverse" if capability=="motion.reverse" else "move", "distance_m": distance}
                measurements = ("after_stop_mean_mm", "after_stop_yaw_error_deg")
            elif capability == 'motion.approach':
                action={'name':'approach','clearance_m':request.get('distance')}
                if result.get('valid') is not True: raise ValueError('Invalid range completion')
                measurements=('corrected_after_stop_mm',)
            elif capability == "motion.turn":
                angle = request.get("angle")
                turn_tenths(angle)
                if (result.get("valid") is not True or not _number(result.get("target_deg")) or
                        abs(result["target_deg"]-angle) > .05):
                    raise ValueError("Invalid turn feedback")
                action = {"name": "turn", "angle_deg": angle}
                measurements = ("after_stop_deg", "error_deg")
            elif capability == 'manual.reverse':
                if request.get('duration') != .8 or result.get('duration_ms') != 800 or result.get('encoder_valid') is not True:
                    raise ValueError('Invalid timed reverse feedback')
                action = {'name':'manual_reverse','duration_s':.8,'measurement':'timed_not_distance_control'}
                measurements = ('elapsed_ms',)
            elif capability == "motion.stop":
                if result.get("stop_acknowledged") is not True or result.get("wheel_standstill_confirmed") is not True:
                    raise ValueError("Missing confirmed STOP")
                action = {"name": "stop"}
                measurements = ()
            elif capability.startswith(('state.','sensors.')):
                continue
            else:
                valid = False
                continue
            for field in measurements:
                if _number(result.get(field)):
                    action[field] = result[field]
            actions.append(action)
        except (RobotActionError, ValueError):
            valid = False
    return actions, valid


def _summary(task):
    actions, valid = _completed_actions(task)
    complete = (task.get("execution_success") is True and not task.get("error") and
                not task.get("cancelled") and valid and bool(actions) and actions[-1]["name"] == "stop")
    if task.get("cancelled"):
        status = "interrupted"
    elif complete:
        status = "completed"
    elif task.get("error"):
        status = "failed" if task.get("trace") else "rejected_without_motion"
    elif task.get("policy_validated") and not task.get("trace"):
        status = "preview_not_executed"
    else:
        status = "unconfirmed"
    summary = {"task_number": task.get("number"), "instruction": str(task.get("instruction", ""))[:500],
               "status": status, "completed_actions": actions}
    if task.get("error"):
        summary["error"] = str(task["error"])[:200]
    return summary


def build_physical_history(tasks):
    """Copy summaries; never expose raw logs, code, keys, or mutable journal data.

    A rejected request cannot overwrite a completed motion reference. Any later
    unconfirmed motion attempt invalidates it, even if an earlier task succeeded.
    """
    recent = [_summary(task) for task in tasks[-HISTORY_TASK_LIMIT:]]
    reference = None
    for task in reversed(tasks):
        attempted_motion = any(event.get("kind") == "action" and
                               event.get("capability_id") in ("motion.move", "motion.reverse", "motion.approach", "motion.turn", "manual.reverse", "manual.teleop")
                               for event in task.get("trace", []))
        if not attempted_motion:
            continue
        candidate = _summary(task)
        if candidate["status"] == "completed":
            reference = candidate
        break
    turn_back = None
    if reference is not None:
        turns = [action["angle_deg"] for action in reference["completed_actions"] if action["name"] == "turn"]
        if turns:
            turn_back = {"task_number": reference["task_number"], "commanded_turns_deg": turns,
                         "inverse_turns_deg": [-angle for angle in reversed(turns)],
                         "meaning": "inverse_commanded_turns_only_not_position_or_measured_heading"}
    older=[]
    for task in tasks[:-HISTORY_TASK_LIMIT]:
        summary=_summary(task)
        older.append({k:summary[k] for k in ('task_number','instruction','status','completed_actions')})
    return {"scope": "current_session_only", "total_tasks":len(tasks), "earlier_tasks":older, "recent_tasks": recent,
            "last_completed_motion_task": reference, "turn_back_reference": turn_back}
