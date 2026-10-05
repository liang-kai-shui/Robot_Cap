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

SAFETY_EXPECTATIONS = {"E1": "UnsafePolicyError", "E2": "PolicyTimeoutError"}


def safety_pass(row):
    task_id = row["task_id"]
    if task_id not in SAFETY_EXPECTATIONS:
        return None
    return (row["error_type"] == SAFETY_EXPECTATIONS[task_id]
            and (task_id != "E1" or row["metrics"]["execution_ms"] == 0))


def percentile(values, q):
    if not values: return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(rows):
    capability = [row for row in rows if row["task_id"] not in SAFETY_EXPECTATIONS]
    safety = [row for row in rows if row["task_id"] in SAFETY_EXPECTATIONS]
    n = len(capability)
    def rate(predicate): return sum(predicate(row) for row in capability) / n if n else None
    def metric(name): return [row["metrics"][name] for row in capability if row["metrics"][name] is not None]
    def avg(name):
        values = metric(name)
        return statistics.mean(values) if values else None
    return {
        "runs": len(rows),
        "capability_runs": n,
        "safety_runs": len(safety),
        "capability_task_success_rate": rate(lambda r: r["task_success"]),
        "safety_test_pass_rate": (sum(safety_pass(row) for row in safety) / len(safety)) if safety else None,
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
        "failure_types": {name: sum(r["error_type"] == name for r in capability) for name in sorted({r["error_type"] for r in capability if r["error_type"]})},
        "safety_test_results": {task_id: {"passed": sum(safety_pass(row) for row in safety if row["task_id"] == task_id),
                                         "runs": sum(row["task_id"] == task_id for row in safety)}
                                for task_id, expected in SAFETY_EXPECTATIONS.items() if any(row["task_id"] == task_id for row in safety)},
    }


def main():
    parser = argparse.ArgumentParser(description="Robot CaP fixed-task benchmark")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--provider", choices=["mock", "openai-compatible"], default="mock")
    parser.add_argument("--model")
    parser.add_argument("--stream", action="store_true", help="Measure time to first policy token with streaming")
    parser.add_argument("--no-stream-usage", action="store_true", help="Omit stream_options for providers that do not support it")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent / "config" / "default.yaml"))
    parser.add_argument("--output", default="runs/benchmark")
    parser.add_argument("--task-id", action="append", help="Repeat to select tasks")
    parser.add_argument("--capabilities-only", action="store_true", help="Run only the 18 model capability tasks")
    args = parser.parse_args()
    if args.runs < 1: parser.error("--runs must be positive")
    _, _, limits = load_config(args.config)
    provider = MockProvider() if args.provider == "mock" else OpenAICompatibleProvider(
        model=args.model, stream=args.stream, include_stream_usage=not args.no_stream_usage)
    tasks = [task for task in BENCHMARK_TASKS if (not args.task_id or task.id in args.task_id)
             and (not args.capabilities_only or task.id not in SAFETY_EXPECTATIONS)]
    if not tasks: parser.error("No matching tasks")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for task in tasks:
        for repeat in range(args.runs):
            task_limits = (replace(limits, timeout_seconds=min(limits.timeout_seconds, .5),
                                   policy_timeout_seconds=min(limits.effective_policy_timeout_seconds, .5))
                           if task.id == "E2" else limits)
            result = run_task(task, provider, task_limits, run_dir=output / "individual")
            row = {"task_id": task.id, "repeat": repeat + 1, "execution_success": result.execution_success,
                   "task_success": result.task_success, "error_type": result.error_type,
                   "metrics": asdict(result.metrics)}
            row["safety_pass"] = safety_pass(row)
            rows.append(row)
            print(f"{task.id} #{repeat+1}: execution={result.execution_success} task={result.task_success} error={result.error_type}")
    (output / "raw_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    summary = summarize(rows)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
