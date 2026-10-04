import argparse
import json
import statistics
from dataclasses import asdict, replace
from pathlib import Path
from agent.runner import run_task
from main import load_config
from providers.mock import MockProvider
from providers.openai_compatible import OpenAICompatibleProvider
from task.benchmark_tasks import BENCHMARK_TASKS


def percentile(values, q):
    if not values: return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(rows):
    n = len(rows)
    def rate(predicate): return sum(predicate(row) for row in rows) / n if n else 0
    def metric(name): return [row["metrics"][name] for row in rows if row["metrics"][name] is not None]
    def avg(name):
        values = metric(name)
        return statistics.mean(values) if values else None
    return {
        "runs": n,
        "task_success_rate": rate(lambda r: r["task_success"]),
        "execution_success_rate": rate(lambda r: r["execution_success"]),
        "validation_failure_rate": rate(lambda r: r["error_type"] in ("UnsafePolicyError", "PolicySyntaxError")),
        "timeout_rate": rate(lambda r: r["error_type"] == "PolicyTimeoutError"),
        "collision_rate": rate(lambda r: r["error_type"] == "CollisionError"),
        "median_llm_latency_ms": percentile(metric("llm_total_ms"), .5),
        "p95_llm_latency_ms": percentile(metric("llm_total_ms"), .95),
        "median_total_latency_ms": percentile(metric("total_ms"), .5),
        "p95_total_latency_ms": percentile(metric("total_ms"), .95),
        "average_input_tokens": avg("input_tokens"),
        "average_output_tokens": avg("output_tokens"),
        "average_policy_lines": avg("policy_lines"),
        "average_action_count": avg("action_count"),
        "failure_types": {name: sum(r["error_type"] == name for r in rows) for name in sorted({r["error_type"] for r in rows if r["error_type"]})},
    }


def main():
    parser = argparse.ArgumentParser(description="Robot CaP fixed-task benchmark")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--provider", choices=["mock", "openai-compatible"], default="mock")
    parser.add_argument("--model")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent / "config" / "default.yaml"))
    parser.add_argument("--output", default="runs/benchmark")
    parser.add_argument("--task-id", action="append", help="Repeat to select tasks")
    args = parser.parse_args()
    if args.runs < 1: parser.error("--runs must be positive")
    _, _, limits = load_config(args.config)
    provider = MockProvider() if args.provider == "mock" else OpenAICompatibleProvider(model=args.model)
    tasks = [task for task in BENCHMARK_TASKS if not args.task_id or task.id in args.task_id]
    if not tasks: parser.error("No matching tasks")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for task in tasks:
        for repeat in range(args.runs):
            task_limits = replace(limits, timeout_seconds=min(limits.timeout_seconds, .5)) if task.id == "E2" else limits
            result = run_task(task, provider, task_limits, run_dir=output / "individual")
            row = {"task_id": task.id, "repeat": repeat + 1, "execution_success": result.execution_success,
                   "task_success": result.task_success, "error_type": result.error_type, "metrics": asdict(result.metrics)}
            rows.append(row)
            print(f"{task.id} #{repeat+1}: execution={result.execution_success} task={result.task_success} error={result.error_type}")
    (output / "raw_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    summary = summarize(rows)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
