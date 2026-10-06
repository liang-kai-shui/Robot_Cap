"""Paired P3 benchmark with common observed navigation and physical-action budgets."""
import argparse
import hashlib
import json
import statistics
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from agent.mission import MissionRunner
from benchmark import percentile
from main import load_config
from navigation.observation import RangeObservation
from navigation.planner import LocalNavigator, NavigationGoal
from providers.base import LLMResponse
from providers.openai_compatible import OpenAICompatibleProvider
from task.mission_benchmark_tasks import MISSION_SCENARIOS
from task.waypoint_evaluator import evaluate_waypoints


class ReferenceMissionProvider:
    """Explicit oracle task parsing and observed-only actions for harness checks."""
    def __init__(self, order):
        self.order = order
        self.planner = None

    def generate_policy(self, task, robot_api, world_state, system_prompt=None):
        started = time.perf_counter()
        context = json.loads(world_state)
        if "public_targets" in context:
            completed = len(context["feedback"]["completed_targets"])
            text = json.dumps({"targets": list(self.order[completed:])})
        else:
            observation = context["current"]
            goal = NavigationGoal(**observation["current_goal"])
            reading = RangeObservation.from_dict(observation["range_observation"])
            if self.planner is None:
                self.planner = LocalNavigator(goal, (reading.pose.x, reading.pose.y))
            self.planner.set_goal(goal)
            action = self.planner.decide(reading)
            if action is None:
                text = "robot.stop()"
            else:
                text = f"robot.{action.operation}({action.value!r})"
        return LLMResponse(text, "reference-oracle", None, None, None,
                           (time.perf_counter() - started) * 1000)


def summarize(rows):
    return {strategy: {
        "runs": len(group), "mission_successes": sum(row["mission_success"] for row in group),
        "final_goal_successes": sum(row["task_success"] for row in group),
        "correct_initial_plans": sum(row["plan_order_match"] for row in group),
        "collisions": sum(row["collision"] for row in group),
        "guard_rejections": sum(row["guard_rejections"] for row in group),
        "median_total_ms": percentile([row["total_ms"] for row in group], .5),
        "p95_total_ms": percentile([row["total_ms"] for row in group], .95),
        "average_actions": statistics.mean(row["actions"] for row in group),
        "model_calls": sum(row["llm_calls"] for row in group),
        "median_model_calls": statistics.median(row["llm_calls"] for row in group),
        "reported_input_tokens": sum(row["input_tokens"] or 0 for row in group),
        "reported_output_tokens": sum(row["output_tokens"] or 0 for row in group),
        "errors": dict(Counter(row["error_type"] for row in group if row["error_type"])),
        "by_task": {task_id: {"runs": len(items), "successes": sum(item["mission_success"] for item in items)}
                    for task_id in sorted({item["task_id"] for item in group})
                    for items in [[item for item in group if item["task_id"] == task_id]]},
    } for strategy in sorted({row["strategy"] for row in rows})
      for group in [[row for row in rows if row["strategy"] == strategy]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("reference", "openai-compatible"), default="reference")
    parser.add_argument("--strategy", choices=("reference", "hybrid", "direct"), action="append")
    parser.add_argument("--model")
    parser.add_argument("--reasoning-effort", default="low", choices=("none", "low", "high", "max"))
    parser.add_argument("--request-timeout", type=float, default=30)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--max-actions", type=int, default=80)
    parser.add_argument("--max-observations", type=int, default=160)
    parser.add_argument("--max-plan-requests", type=int, default=4)
    parser.add_argument("--task-id", action="append")
    parser.add_argument("--output", default="runs/mission-benchmark")
    parser.add_argument("--config", default=str(Path(__file__).parent / "config/default.yaml"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if min(args.runs, args.max_actions, args.max_observations, args.max_plan_requests) < 1:
        parser.error("Budgets and repeats must be positive")
    scenarios = [item for item in MISSION_SCENARIOS if not args.task_id or item.task.id in args.task_id]
    if not scenarios:
        parser.error("No matching missions")
    strategies = list(dict.fromkeys(args.strategy or ("reference", "hybrid", "direct")))
    _, _, limits = load_config(args.config)
    runner = MissionRunner(limits, args.max_actions, args.max_observations, args.max_plan_requests)
    source_root = Path(__file__).parent
    sources = sorted(path for package in ("agent", "navigation", "runtime", "robot", "task")
                     for path in (source_root / package).rglob("*.py")) + [Path(__file__)]
    signature = hashlib.sha256(b"".join(path.relative_to(source_root).as_posix().encode()
                                        + path.read_bytes() for path in sources)).hexdigest()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    metadata = {"suite": "paired-mission-p3-v1", "scorer_version": "waypoints-v2",
                "provider": args.provider, "reference_only": args.provider == "reference",
                "source_signature": signature, "runtime_limits": asdict(limits),
                "model": args.model, "reasoning_effort": args.reasoning_effort,
                "request_timeout_seconds": args.request_timeout, "runs_per_task": args.runs,
                "strategies": strategies, "task_ids": [item.task.id for item in scenarios],
                "action_budget": runner.navigation.limits.max_actions, "observation_budget": args.max_observations,
                "plan_request_budget": args.max_plan_requests,
                "action_request_budget": runner.max_action_requests}
    manifest = output / "experiment.json"
    raw = output / "raw_results.jsonl"
    rows = []
    if manifest.exists():
        if not args.resume or json.loads(manifest.read_text(encoding="utf-8")) != metadata:
            parser.error("Existing experiment: use --resume with exactly matching configuration or a new output")
        if raw.exists():
            rows = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
    else:
        manifest.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (output / "scenarios.json").write_text(json.dumps([asdict(item) for item in scenarios],
                                                ensure_ascii=False, indent=2), encoding="utf-8")
    finished = {(row["strategy"], row["task_id"], row["repeat"]) for row in rows}
    for item in scenarios:
        for repeat in range(1, args.runs + 1):
            for strategy in strategies:
                if (strategy, item.task.id, repeat) in finished:
                    continue
                provider = None
                if strategy != "reference":
                    provider = (ReferenceMissionProvider(item.expected_order) if args.provider == "reference" else
                        OpenAICompatibleProvider(model=args.model, request_timeout_seconds=args.request_timeout,
                                                 reasoning_effort=args.reasoning_effort))
                result = runner.run(item.task, item.public_targets, strategy=strategy, provider=provider,
                                    reference_order=item.expected_order if strategy == "reference" else None)
                score = evaluate_waypoints(result.trace, result.initial_state,
                                           [item.public_targets[name] for name in item.expected_order])
                row = {"task_id": item.task.id, "category": item.category, "repeat": repeat,
                       "strategy": strategy, "task_success": result.task_success,
                       "mission_success": result.task_success and score["stopped_order_success"],
                       "plan_order_match": bool(result.plans and result.plans[0].get("targets") == list(item.expected_order)),
                       "error_type": result.error_type, "error_message": result.error_message,
                       "collision": result.final_state["collision"], "stopped": result.final_state["stopped"],
                       **score, **result.metrics}
                payload = {**result.to_dict(), "evaluation": score}
                (output / f"{strategy}-{item.task.id}-{repeat}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                rows.append(row)
                with raw.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                (output / "summary.json").write_text(json.dumps({**metadata, "results": summarize(rows)},
                                                            ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"{strategy} {item.task.id} #{repeat}: mission={row['mission_success']} "
                      f"actions={row['actions']} calls={row['llm_calls']} error={row['error_type']}", flush=True)
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
