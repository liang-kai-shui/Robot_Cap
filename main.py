import argparse
import json
from dataclasses import asdict
from pathlib import Path
from agent.runner import run_task
from providers.mock import MockProvider
from providers.openai_compatible import OpenAICompatibleProvider
from robot.state import Pose
from runtime.limits import RuntimeLimits
from task.benchmark_tasks import BENCHMARK_TASKS
from task.task import Task
from world.obstacle import Obstacle
from world.world import VirtualWorld


def load_config(path: str):
    import yaml
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    world = data.get("world", {})
    robot = data.get("robot", {})
    obstacles = tuple(Obstacle(**item) for item in data.get("obstacles", []))
    return VirtualWorld(world.get("width", 10), world.get("height", 10), obstacles), Pose(**robot), RuntimeLimits(**data.get("runtime", {}))


def main():
    parser = argparse.ArgumentParser(description="Robot CaP V0")
    parser.add_argument("instruction", nargs="?")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--provider", choices=["mock", "openai-compatible"], default="mock")
    parser.add_argument("--model")
    parser.add_argument("--stream", action="store_true", help="Measure time to first policy token with streaming")
    parser.add_argument("--no-stream-usage", action="store_true", help="Omit stream_options for providers that do not support it")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent / "config" / "default.yaml"))
    parser.add_argument("--task-id", help="Use a benchmark task and its structured success condition")
    parser.add_argument("--success-file", help="JSON success spec for a custom instruction")
    args = parser.parse_args()
    world, pose, limits = load_config(args.config)
    if args.task_id:
        task = next((item for item in BENCHMARK_TASKS if item.id == args.task_id), None)
        if task is None: parser.error("Unknown benchmark task ID")
    else:
        instruction = args.instruction or input("Robot CaP V0\nTask > ").strip()
        if not instruction: parser.error("Task instruction is empty")
        task = next((item for item in BENCHMARK_TASKS if item.instruction == instruction), None)
        if task is None:
            success = json.loads(Path(args.success_file).read_text(encoding="utf-8")) if args.success_file else {"type": "unverified"}
            task = Task("custom", instruction, pose, success, world)
    provider = MockProvider() if args.provider == "mock" else OpenAICompatibleProvider(
        model=args.model, stream=args.stream, include_stream_usage=not args.no_stream_usage)

    def show_policy(policy):
        print("\nGenerated Policy:\n-----------------\n" + policy + "\n\nValidation: PASS")

    def confirm(policy):
        return input("Execute? [Y/n] ").strip().lower() in ("", "y", "yes")

    result = run_task(task, provider, limits, on_policy=show_policy,
                      on_validated=None if args.auto else confirm)
    if result.error_type and not result.policy:
        print("Generation/validation failed:", result.error_type, result.error_message)
    elif result.error_type and result.metrics.execution_ms == 0:
        print("Validation/execution failed:", result.error_type, result.error_message)
    print("Final State:", json.dumps(asdict(result.final_state), ensure_ascii=False))
    print("Execution Success:", result.execution_success)
    print("Task Success:", result.task_success)
    if task.success.get("type") == "unverified":
        print("Task Success is unverified: no structured success condition was supplied.")
    print("Metrics:", json.dumps(asdict(result.metrics), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
