"""Independent known-map benchmark; does not read episodes or alter the V0/V1 suite."""
import argparse
import json
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from agent.runner import run_task
from benchmark import percentile
from main import load_config
from providers.base import LLMProvider, LLMResponse
from providers.openai_compatible import OpenAICompatibleProvider
from task.complex_benchmark_tasks import COMPLEX_SCENARIOS
from task.complex_evaluator import evaluate_complex


class ReferenceProvider(LLMProvider):
    """Known solutions for harness/solvability checks, never a model score."""

    def generate_policy(self, task, robot_api, world_state, system_prompt=None):
        started = time.perf_counter()
        policy = next(item.reference_policy for item in COMPLEX_SCENARIOS
                      if item.task.instruction == task)
        return LLMResponse(policy, "reference-oracle", None, None, None,
                           (time.perf_counter() - started) * 1000)


def summarize(rows):
    def rate(items, key):
        return sum(bool(row[key]) for row in items) / len(items) if items else None

    def metric(name):
        return [row["metrics"][name] for row in rows if row["metrics"][name] is not None]

    def average(name):
        values = metric(name)
        return statistics.mean(values) if values else None

    total = [row["metrics"]["total_ms"] for row in rows]
    categories = sorted({row["category"] for row in rows})
    return {
        "runs": len(rows),
        "execution_success_rate": rate(rows, "execution_success"),
        "task_success_rate": rate(rows, "task_success"),
        "complex_success_rate": rate(rows, "complex_success"),
        "median_total_ms": percentile(total, .5),
        "p95_total_ms": percentile(total, .95),
        "median_llm_ms": percentile(metric("llm_total_ms"), .5),
        "p95_llm_ms": percentile(metric("llm_total_ms"), .95),
        "average_input_tokens": average("input_tokens"),
        "average_output_tokens": average("output_tokens"),
        "average_actions": statistics.mean(row["metrics"]["action_count"] for row in rows) if rows else None,
        "by_category": {category: {"runs": len(items), "complex_success_rate": rate(items, "complex_success")}
                        for category in categories
                        for items in [[row for row in rows if row["category"] == category]]},
        "error_types": {name: sum(row["error_type"] == name for row in rows)
                        for name in sorted({row["error_type"] for row in rows if row["error_type"]})},
        "criteria_failures": {name: sum(name in row["criteria_failures"] for row in rows)
                              for name in sorted({failure for row in rows for failure in row["criteria_failures"]})},
    }


def main():
    parser = argparse.ArgumentParser(description="Independent complex known-map Robot_Cap benchmark")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--provider", choices=("reference", "openai-compatible"), default="reference")
    parser.add_argument("--model")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--no-stream-usage", action="store_true")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent / "config" / "default.yaml"))
    parser.add_argument("--output", default="runs/complex-benchmark")
    parser.add_argument("--task-id", action="append", help="Repeat to select scenarios")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    _, _, limits = load_config(args.config)
    provider = (ReferenceProvider() if args.provider == "reference" else
                OpenAICompatibleProvider(model=args.model, stream=args.stream,
                                         include_stream_usage=not args.no_stream_usage))
    scenarios = [item for item in COMPLEX_SCENARIOS if not args.task_id or item.task.id in args.task_id]
    if not scenarios:
        parser.error("No matching complex scenarios")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for scenario in scenarios:
        for repeat in range(args.runs):
            result = run_task(scenario.task, provider, limits, run_dir=output / "individual")
            complex_success, failures = evaluate_complex(scenario, result)
            row = {"task_id": scenario.task.id, "category": scenario.category, "repeat": repeat + 1,
                   "execution_success": result.execution_success, "task_success": result.task_success,
                   "complex_success": complex_success, "criteria_failures": failures,
                   "error_type": result.error_type, "metrics": asdict(result.metrics)}
            rows.append(row)
            print(f"{scenario.task.id} #{repeat + 1}: execution={result.execution_success} "
                  f"goal={result.task_success} complex={complex_success} failures={failures}")
    (output / "raw_results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    summary = {"suite": "complex-known-map-v1", "provider": args.provider,
               "reference_only": args.provider == "reference", **summarize(rows)}
    if args.provider == "reference":
        summary["median_llm_ms"] = None
        summary["p95_llm_ms"] = None
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
