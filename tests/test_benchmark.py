from benchmark import summarize


def row(task_id, task_success, error_type=None):
    return {"task_id": task_id, "task_success": task_success, "execution_success": error_type is None,
            "error_type": error_type, "metrics": {"llm_total_ms": 10.0, "total_ms": 20.0,
            "input_tokens": None, "output_tokens": None, "policy_lines": 2, "action_count": 1,
            "execution_ms": 0}}


def test_capability_and_safety_rates_are_separate():
    summary = summarize([row("A1", True), row("A2", False),
                         row("E1", False, "UnsafePolicyError"), row("E2", False, "PolicyTimeoutError")])
    assert summary["runs"] == 4
    assert summary["capability_runs"] == 2
    assert summary["safety_runs"] == 2
    assert summary["capability_task_success_rate"] == .5
    assert summary["safety_test_pass_rate"] == 1.0
    assert summary["validation_failure_rate"] == 0.0
    assert summary["timeout_rate"] == 0.0


def test_safety_failure_is_visible():
    summary = summarize([row("E1", False, "PolicyExecutionError")])
    assert summary["capability_task_success_rate"] is None
    assert summary["safety_test_pass_rate"] == 0
