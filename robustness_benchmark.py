"""P4 seeded static-world perturbations, with independent private truth scoring."""
import argparse
import hashlib
import json
import math
import statistics
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from agent.mission import MissionRunner
from benchmark import percentile
from main import load_config
from robot.backends.perturbed import PROFILES, PerturbedVirtualBackend
from task.evaluator import TaskEvaluator
from task.mission_benchmark_tasks import MISSION_SCENARIOS, MissionScenario
from task.navigation_benchmark_tasks import generate_navigation_scenarios
from task.waypoint_evaluator import evaluate_waypoints


def evaluate_truth(result, scenario, backend, initial_truth):
    """Never used by the planner, worker, completion check or action guard."""
    final = backend.truth_snapshot()
    score = evaluate_waypoints(backend.truth_events, asdict(initial_truth),
        [scenario.public_targets[name] for name in scenario.expected_order])
    final_goal = TaskEvaluator().evaluate(scenario.task, final)
    geometric_success = score["stopped_order_success"] and final_goal and not final.collision and final.stopped
    reported = result.navigation_success
    estimate = result.final_state["pose"]
    return {**score, "actual_final_goal_success": final_goal,
        "actual_mission_success": geometric_success and reported and result.execution_success,
        "reported_completion": reported, "false_completion": reported and not geometric_success,
        "safe_abort": not reported and final.stopped and not final.collision,
        "collision_attempt": final.collision, "stopped": final.stopped,
        "pose_error_m": math.hypot(estimate["x"] - final.pose.x, estimate["y"] - final.pose.y),
        "heading_error_degrees": abs((estimate["heading"] - final.pose.heading + 180) % 360 - 180),
        "actual_distance_m": sum(abs(event["request"]["actual"]) for event in backend.truth_events
            if event["phase"] == "completed" and event["capability_id"] == "motion.move"),
        "sensor_samples": backend.sensor_samples, "sensor_dropouts": backend.sensor_dropouts,
        "cancellations": backend.cancellations}


def run_scenario(runner, scenario, profile, seed, **session_options):
    backend = PerturbedVirtualBackend(scenario.task.world, scenario.task.initial,
        runner.navigation.limits, profile=profile, seed=seed)
    initial_truth = backend.truth_snapshot()
    result = runner.run(scenario.task, scenario.public_targets, strategy="reference",
        reference_order=scenario.expected_order, backend=backend,
        observation_adapter=backend.adapt_observation, heading_tolerance_deg=.25,
        distance_scale_bound=1 + profile.move_error_fraction, max_sensor_retries=2, **session_options)
    score = evaluate_truth(result, scenario, backend, initial_truth)
    payload = {"reported_episode": result.to_dict(), "evaluation": score,
        "private_truth": {"initial_state": asdict(initial_truth),
                          "final_state": asdict(backend.truth_snapshot()), "trace": backend.truth_events}}
    return {"task_id": scenario.task.id, "category": scenario.category,
        "error_type": result.error_type, "error_message": result.error_message,
        **result.metrics, **score}, payload


def summarize(rows):
    results = {}
    for name in sorted({row["profile"] for row in rows}):
        group = [row for row in rows if row["profile"] == name]
        results[name] = {"runs": len(group),
            **{field: sum(row[field] for row in group) for field in (
                "actual_mission_success", "reported_completion", "false_completion", "safe_abort",
                "collision_attempt", "stopped", "sensor_samples", "sensor_dropouts", "sensor_retries", "guard_rejections")},
            "average_actions": statistics.mean(row["actions"] for row in group),
            "median_total_ms": percentile([row["total_ms"] for row in group], .5),
            "p95_total_ms": percentile([row["total_ms"] for row in group], .95),
            "max_pose_error_m": max(row["pose_error_m"] for row in group),
            "model_calls": sum(row["llm_calls"] for row in group),
            "errors": dict(Counter(row["error_type"] for row in group if row["error_type"])),
            "by_task": {task_id: {"runs": len(items),
                "successes": sum(item["actual_mission_success"] for item in items),
                "false_completions": sum(item["false_completion"] for item in items)}
                for task_id in sorted({row["task_id"] for row in group})
                for items in [[row for row in group if row["task_id"] == task_id]]}}
    return results


def source_signature(root):
    sources = sorted(path for path in root.rglob("*.py")
        if path.relative_to(root).parts[0] in (
            "agent", "navigation", "runtime", "robot", "task", "world", "providers", "metrics")
        or path.parent == root)
    return hashlib.sha256(b"".join(path.relative_to(root).as_posix().encode() + b"\0" +
        path.read_bytes() + b"\0" for path in sources)).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=tuple(PROFILES), action="append")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261006)
    parser.add_argument("--random-worlds", type=int, default=0)
    parser.add_argument("--world-seed", type=int, default=271828)
    parser.add_argument("--task-id", action="append")
    parser.add_argument("--max-actions", type=int, default=80)
    parser.add_argument("--max-observations", type=int, default=160)
    parser.add_argument("--output", default="runs/robustness-benchmark")
    parser.add_argument("--config", default=str(Path(__file__).parent / "config/default.yaml"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if min(args.runs, args.max_actions, args.max_observations) < 1 or args.random_worlds < 0:
        parser.error("Budgets/repeats must be positive; random-worlds must be nonnegative")
    scenarios = (tuple(MissionScenario(item.task, {"target": item.goal}, ("target",), item.category)
        for item in generate_navigation_scenarios(args.random_worlds, args.world_seed))
        if args.random_worlds else MISSION_SCENARIOS)
    if args.task_id:
        unknown = set(args.task_id) - {item.task.id for item in scenarios}
        if unknown:
            parser.error(f"Unknown task IDs: {sorted(unknown)}")
        scenarios = tuple(item for item in scenarios if item.task.id in args.task_id)
    names = list(dict.fromkeys(args.profile or PROFILES))
    _, _, limits = load_config(args.config)
    runner = MissionRunner(limits, args.max_actions, args.max_observations)
    metadata = {"suite": "static-robustness-p4-v1", "scorer_version": "private-truth-waypoints-v1",
        "source_signature": source_signature(Path(__file__).parent),
        "reference_oracle_order": True, "paid_model_calls": 0,
        "runtime_limits": asdict(runner.navigation.limits), "seed": args.seed,
        "world_seed": args.world_seed if args.random_worlds else None,
        "random_worlds": args.random_worlds, "runs_per_task": args.runs,
        "task_ids": [item.task.id for item in scenarios],
        "profiles": {name: asdict(PROFILES[name]) for name in names},
        "navigation": {"heading_tolerance_deg": .25, "resolution_m": runner.navigation.resolution,
            "max_sensor_retries": 2,
            "margin_m": runner.navigation.margin, "max_step_m": runner.navigation.max_step,
            "max_observation_age_seconds": runner.navigation.max_observation_age,
            "observation_budget": args.max_observations,
            "distance_scale_bound": "1 + profile.move_error_fraction"}}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest, raw = output / "experiment.json", output / "raw_results.jsonl"
    rows = []
    if manifest.exists():
        if not args.resume or json.loads(manifest.read_text(encoding="utf-8")) != metadata:
            parser.error("Use a new output or --resume with exactly matching configuration and sources")
        if raw.exists():
            rows = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
    else:
        if raw.exists():
            parser.error("Raw results without a manifest; use a new output")
        manifest.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        (output / "scenarios.json").write_text(json.dumps([asdict(item) for item in scenarios],
            ensure_ascii=False, indent=2), encoding="utf-8")
    finished = {(row["profile"], row["task_id"], row["repeat"]) for row in rows}
    for index, item in enumerate(scenarios):
        for repeat in range(1, args.runs + 1):
            # Same task/repeat seed across profiles; independent RNG streams mean
            # adding sensor noise does not shift the actuator's random sequence.
            seed = args.seed + index * 10000 + repeat
            for name in names:
                if (name, item.task.id, repeat) in finished:
                    continue
                row, payload = run_scenario(runner, item, PROFILES[name], seed)
                row.update(profile=name, repeat=repeat, fault_seed=seed)
                payload.update(profile=name, repeat=repeat, fault_seed=seed,
                    profile_parameters=asdict(PROFILES[name]), source_signature=metadata["source_signature"])
                (output / f"{name}-{item.task.id}-{repeat}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
                rows.append(row)
                with raw.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                (output / "summary.json").write_text(json.dumps({**metadata, "results": summarize(rows)},
                    ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
                print(f"{name} {item.task.id} #{repeat}: actual={row['actual_mission_success']} "
                    f"reported={row['reported_completion']} false={row['false_completion']} "
                    f"actions={row['actions']} error={row['error_type']}", flush=True)
    print(json.dumps(summarize(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
