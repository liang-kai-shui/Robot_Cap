"""Independent observed-map navigation benchmark (P1/P2, no model calls)."""
import argparse
import json
import statistics
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from benchmark import percentile
from main import load_config
from navigation.runner import NavigationRunner
from task.navigation_benchmark_tasks import NAVIGATION_SCENARIOS, generate_navigation_scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", action="append")
    parser.add_argument("--max-actions", type=int, default=80)
    parser.add_argument("--max-observations", type=int, default=160)
    parser.add_argument("--resolution", type=float, default=.25)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--random-worlds", type=int, default=0, help="Replace fixed scenes with seeded evaluation scenes")
    parser.add_argument("--seed", type=int, default=20261006)
    parser.add_argument("--output", default="runs/navigation-benchmark")
    parser.add_argument("--config", default=str(Path(__file__).parent / "config/default.yaml"))
    args = parser.parse_args()
    if min(args.runs, args.max_actions, args.max_observations) < 1:
        parser.error("Run and navigation budgets must be positive")
    if args.random_worlds < 0:
        parser.error("--random-worlds must be nonnegative")
    source = generate_navigation_scenarios(args.random_worlds, args.seed) if args.random_worlds else NAVIGATION_SCENARIOS
    scenarios = [item for item in source if not args.task_id or item.task.id in args.task_id]
    if not scenarios:
        parser.error("No matching navigation tasks")
    _, _, limits = load_config(args.config)
    runner = NavigationRunner(limits, args.max_actions, args.max_observations, args.resolution)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    # Private benchmark manifests aid reproducibility; they are never sent to the planner.
    (output / "scenarios.json").write_text(json.dumps([asdict(item) for item in scenarios],
                                                     ensure_ascii=False, indent=2), encoding="utf-8")
    rows = []
    for item in scenarios:
        for repeat in range(1, args.runs + 1):
            result = runner.run(item.task, item.goal)
            row = {"task_id": item.task.id, "repeat": repeat, "category": item.category,
                   "expected_reachable": item.expected_reachable, "task_success": result.task_success,
                   "error_type": result.error_type, "error_message": result.error_message,
                   "collision": result.final_state["collision"], "stopped": result.final_state["stopped"],
                   **result.metrics}
            rows.append(row)
            (output / f"{item.task.id}-{repeat}.json").write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{item.task.id} #{repeat}: success={result.task_success} "
                  f"actions={result.metrics['actions']} error={result.error_type}", flush=True)
    reachable = [row for row in rows if row["expected_reachable"]]
    unreachable = [row for row in rows if not row["expected_reachable"]]
    summary = {"suite": "observed-map-navigation-v1", "planner": "astar-frontier",
               "random_worlds": args.random_worlds, "seed": args.seed if args.random_worlds else None,
               "model_evaluation": False, "llm_calls": 0, "resolution_m": args.resolution,
               "action_budget": runner.limits.max_actions, "observation_budget": args.max_observations,
               "runs": len(rows), "reachable_runs": len(reachable),
               "reachable_successes": sum(row["task_success"] for row in reachable),
               "reachable_success_rate": statistics.mean(row["task_success"] for row in reachable) if reachable else None,
               "unreachable_safe_endings": sum(not row["task_success"] and row["stopped"]
                                                and not row["collision"] and row["error_type"] in (
                                                    "NavigationInformationExhausted", "NavigationActionBudgetExceeded",
                                                    "NavigationObservationBudgetExceeded", "NavigationStalled") for row in unreachable),
               "collisions": sum(row["collision"] for row in rows),
               "median_total_ms": percentile([row["total_ms"] for row in rows], .5),
               "p95_total_ms": percentile([row["total_ms"] for row in rows], .95),
               "median_nominal_motion_seconds": statistics.median(row["nominal_motion_seconds"] for row in rows),
               "average_actions": statistics.mean(row["actions"] for row in rows),
               "guard_rejections": sum(row["guard_rejections"] for row in rows),
               "errors": dict(Counter(row["error_type"] for row in rows if row["error_type"]))}
    (output / "raw_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
