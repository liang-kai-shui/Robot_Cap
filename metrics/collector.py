import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from runtime.result import ExecutionResult
from task.task import Task
from policy.episode import EpisodeRecord


def save_run(task: Task, result: ExecutionResult, directory: str | Path = "runs") -> Path:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    target = path / f"{stamp}_{task.id}_{uuid4().hex[:8]}.json"
    payload = {
        "task_id": task.id, "instruction": task.instruction,
        "model": result.metrics.model, "policy": result.policy,
        "result": {"execution_success": result.execution_success, "task_success": result.task_success,
                   "error_type": result.error_type, "error_message": result.error_message,
                   "initial_state": asdict(result.initial_state), "final_state": asdict(result.final_state), "logs": result.logs},
        "metrics": asdict(result.metrics),
        "episode": EpisodeRecord.from_run(task, result).to_dict(),
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
