"""Recompute the navigation comparison from saved local episode files (not an API client)."""
import json
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def percentile(values, quantile):
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    left = int(index)
    right = min(left + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (index - left)


def summarize(folder):
    episodes = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("H*-*.json"))]
    assert len(episodes) == 12, (folder, len(episodes))
    decisions = [step for episode in episodes for step in episode["decisions"]]
    llm_times = [step["llm_ms"] for step in decisions if step["llm_ms"] is not None]
    return {
        "raw_directory": str(folder.relative_to(ROOT)).replace("\\", "/"),
        "runs": len(episodes),
        "successes": sum(episode["task_success"] for episode in episodes),
        "median_total_ms": statistics.median([episode["total_ms"] for episode in episodes]),
        "p95_total_ms": percentile([episode["total_ms"] for episode in episodes], .95),
        "median_llm_call_ms": statistics.median(llm_times),
        "llm_calls": len(decisions),
        "input_tokens": sum(episode["input_tokens"] or 0 for episode in episodes),
        "output_tokens": sum(episode["output_tokens"] or 0 for episode in episodes),
        "final_errors": dict(Counter(episode["error_type"] for episode in episodes if episode["error_type"])),
        "decision_errors": dict(Counter(step["error_type"] for step in decisions if step["error_type"])),
        "collisions": sum(episode["final_state"]["collision"] for episode in episodes),
        "guard_rejections": sum(not check["accepted"] for step in decisions for check in step.get("safety_checks", [])),
        "by_task": {task_id: {"successes": sum(ep["task_success"] for ep in episodes if ep["task_id"] == task_id),
                              "runs": sum(ep["task_id"] == task_id for ep in episodes)}
                    for task_id in ("H1", "H2", "H3", "H4")},
        "episodes": [{"task_id": ep["task_id"], "task_success": ep["task_success"],
                      "total_ms": ep["total_ms"], "error_type": ep["error_type"],
                      "error_message": ep.get("error_message"),
                      "steps": [{"policy": step["policy"], "error_type": step["error_type"],
                                 "pose_before": step["observation"].get("pose_estimate"),
                                 "pose_after": step.get("final_pose"), "safety_checks": step.get("safety_checks", [])}
                                for step in ep["decisions"]]} for ep in episodes],
    }


if __name__ == "__main__":
    conditions = {
        "old_nonthinking": "runs/deepseek-flash-20261005/nonthinking_interactive",
        "intermediate_prompt_none": "runs/deepseek-interactive-v2-20261005",
        "final_prompt_none": "runs/deepseek-interactive-v2-concise-20261005",
        "final_prompt_low": "runs/deepseek-interactive-v2-low-20261005",
    }
    report = {name: summarize(ROOT / folder) for name, folder in conditions.items()}
    destination = ROOT / "reports/interactive_optimization_2026-10-05_summary.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({name: {key: value for key, value in data.items() if key != "episodes"}
                      for name, data in report.items()}, ensure_ascii=False, indent=2))
