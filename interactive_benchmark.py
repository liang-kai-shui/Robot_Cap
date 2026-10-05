"""Software-only interactive benchmark with maps hidden from the planner."""
import argparse
import json
import statistics
import time
from pathlib import Path
from agent.interactive import InteractiveRunner
from benchmark import percentile
from main import load_config
from providers.base import LLMProvider, LLMResponse
from providers.openai_compatible import OpenAICompatibleProvider
from task.interactive_benchmark_tasks import INTERACTIVE_TASKS


class ReferenceLocalProvider(LLMProvider):
    """Simple local rule for checking the harness, not a model baseline."""

    def generate_policy(self, task, robot_api, world_state, system_prompt=None):
        started = time.perf_counter()
        current = json.loads(world_state)["current"]
        pose = current["pose_estimate"]
        x, y, heading = pose["x"], pose["y"], pose["heading"]
        front = current.get("front_distance_m", 0)
        safe = current["navigation_limits"]["safe_forward_distance_m"]
        if y > 1.5:
            if x < 3.99:
                if heading == 90:
                    policy = "robot.turn(-90)"
                else:
                    policy = f"robot.move({min(1.5, round(4-x, 9), safe)})"
            else:
                policy = "robot.turn(-90)" if heading == 0 else "robot.move(1)"
        elif heading == 90:
            policy = "robot.move(1)"
        elif front < 1.2 and x < 3.99:
            policy = "robot.turn(90)"
        else:
            policy = f"robot.move({min(1.5, round(4-x, 9), safe)})"
        return LLMResponse(policy, "reference-local-rule", None, None, None,
                           (time.perf_counter() - started) * 1000)


def summarize(rows):
    latencies = [row["total_ms"] for row in rows]
    return {"runs": len(rows),
            "task_success_rate": sum(row["task_success"] for row in rows) / len(rows),
            "median_total_ms": percentile(latencies, .5),
            "p95_total_ms": percentile(latencies, .95),
            "average_decisions": statistics.mean(row["decisions"] for row in rows),
            "average_actions": statistics.mean(row["actions"] for row in rows),
            "average_llm_calls": statistics.mean(row["decisions"] for row in rows),
            "error_types": {name: sum(row["error_type"] == name for row in rows)
                            for name in sorted({row["error_type"] for row in rows if row["error_type"]})}}


def main():
    parser = argparse.ArgumentParser(description="Local-observation interactive benchmark")
    parser.add_argument("--provider", choices=("reference", "openai-compatible"), default="reference")
    parser.add_argument("--model")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--request-timeout", type=float, help="API socket/read timeout in seconds")
    parser.add_argument("--reasoning-effort", choices=("none", "low", "medium", "high", "xhigh", "max"))
    parser.add_argument("--max-decisions", type=int, default=8)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--task-id", action="append")
    parser.add_argument("--output", default="runs/interactive-benchmark")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent / "config" / "default.yaml"))
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    if args.max_decisions < 1:
        parser.error("--max-decisions must be positive")
    _, _, limits = load_config(args.config)
    provider = (ReferenceLocalProvider() if args.provider == "reference" else
                OpenAICompatibleProvider(model=args.model, stream=args.stream,
                                         request_timeout_seconds=args.request_timeout,
                                         reasoning_effort=args.reasoning_effort))
    tasks = [task for task in INTERACTIVE_TASKS if not args.task_id or task.id in args.task_id]
    if not tasks:
        parser.error("No matching tasks")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for task in tasks:
        for repeat in range(args.runs):
            result = InteractiveRunner(limits, max_decisions=args.max_decisions).run(task, provider)
            row = {"task_id": task.id, "repeat": repeat + 1, "task_success": result.task_success,
                   "error_type": result.error_type, "error_message": result.error_message,
                   "decisions": len(result.decisions),
                   "actions": result.final_state.action_count, "total_ms": result.total_ms,
                   "llm_ms": result.llm_ms, "input_tokens": result.input_tokens,
                   "output_tokens": result.output_tokens}
            rows.append(row)
            (output / f"{task.id}-{repeat+1}.json").write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{task.id} #{repeat+1}: success={result.task_success} decisions={len(result.decisions)} "
                  f"actions={result.final_state.action_count} error={result.error_type}")
    summary = {"suite": "interactive-hidden-map-v2-single-action", "provider": args.provider,
               "model": getattr(provider, "model", "reference-local-rule"),
               "request_timeout_seconds": getattr(provider, "request_timeout_seconds", None),
               "reasoning_effort": getattr(provider, "reasoning_effort", None),
               "max_decisions": args.max_decisions,
               "reference_only": args.provider == "reference", **summarize(rows)}
    (output / "raw_results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
