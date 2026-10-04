import json
from agent.runner import run_task
from providers.mock import MockProvider
from robot.state import Pose
from runtime.limits import RuntimeLimits
from task.benchmark_tasks import BENCHMARK_TASKS
from task.task import Task


def test_mock_chain_and_metrics(tmp_path):
    task = BENCHMARK_TASKS[0]
    result = run_task(task, MockProvider(), run_dir=tmp_path)
    assert result.execution_success and result.task_success
    assert result.metrics.model == "mock"
    assert result.metrics.input_tokens is None and result.metrics.llm_ttft_ms is None
    assert result.metrics.total_ms >= result.metrics.execution_ms
    saved = list(tmp_path.glob("*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text(encoding="utf-8"))["result"]["task_success"]


def test_execution_and_task_success_differ(tmp_path):
    task = Task("stop_only", "stop only", Pose(1, 1, 0), {"type": "pose_near", "x": 3, "y": 1}, fixed_policy="robot.stop()")
    result = run_task(task, MockProvider(), run_dir=tmp_path)
    assert result.execution_success and not result.task_success


def test_unsafe_code_never_starts_worker(tmp_path):
    task = BENCHMARK_TASKS[-2]
    result = run_task(task, MockProvider(), run_dir=tmp_path)
    assert result.error_type == "UnsafePolicyError"
    assert result.metrics.execution_ms == 0


def test_feedback_loop(tmp_path):
    task = next(item for item in BENCHMARK_TASKS if item.id == "D1")
    result = run_task(task, MockProvider(), run_dir=tmp_path)
    assert result.execution_success and result.task_success
    assert result.metrics.action_count > 1
