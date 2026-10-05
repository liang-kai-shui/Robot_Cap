"""Parent-owned sandbox lifecycle and robot command service."""
import json
import multiprocessing as mp
import time
from dataclasses import dataclass, field
from robot.backends.virtual import VirtualBackend
from robot.runtime import RobotRuntime
from robot.state import Pose, RobotState
from runtime.limits import RuntimeLimits
from runtime.validator import PolicyValidator
from runtime.worker import run_worker
from world.world import VirtualWorld


@dataclass
class WorkerOutcome:
    """Execution state and compatibility logs from one policy run."""

    success: bool
    error_type: str | None
    error_message: str | None
    final_state: RobotState
    logs: list[dict]
    execution_ms: float
    trace: list[dict] = field(default_factory=list)


class PolicyExecutor:
    """Spawn a policy worker while retaining backend ownership in the parent."""

    def __init__(self, limits: RuntimeLimits | None = None):
        self.limits = limits or RuntimeLimits()

    def execute(self, policy: str, world: VirtualWorld, initial: Pose, validated: bool = False,
                backend=None) -> WorkerOutcome:
        """Validate and run one policy against a trusted backend."""
        if not validated:
            PolicyValidator().validate(policy)
        runtime = RobotRuntime(backend or VirtualBackend(world, initial, self.limits), self.limits)
        context = mp.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(target=run_worker,
                                  args=(policy, child, self.limits.communication_timeout_seconds))
        start = time.perf_counter()
        success, error_type, message = False, "PolicyExecutionError", "Worker exited without result"
        emergency = False
        try:
            process.start()
            child.close()
            deadline = start + self.limits.policy_timeout_seconds
            while True:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    error_type, message = "PolicyTimeoutError", "Policy exceeded execution timeout"
                    emergency = True
                    break
                if parent.poll(min(remaining, 0.05)):
                    try:
                        packet = json.loads(parent.recv_bytes().decode("utf-8"))
                    except (EOFError, OSError, ValueError):
                        emergency = True
                        break
                    if packet.get("kind") == "outcome":
                        success = bool(packet.get("success"))
                        error_type, message = packet.get("error_type"), packet.get("error_message")
                        break
                    if packet.get("kind") == "command":
                        response = runtime.dispatch(packet)
                        response["command_id"] = packet.get("command_id")
                        try:
                            parent.send_bytes(json.dumps(response).encode("utf-8"))
                        except (EOFError, OSError):
                            emergency = True
                            break
                elif not process.is_alive():
                    emergency = True
                    break
            process.join(timeout=0.05)
            if process.is_alive():
                process.terminate()
                process.join(timeout=0.2)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.5)
            if emergency or process.exitcode not in (0, None):
                runtime.emergency_stop()
            logs = getattr(runtime.backend, "logs", [])
            return WorkerOutcome(success, error_type, message, runtime.snapshot(), list(logs),
                                 (time.perf_counter() - start) * 1000, runtime.trace)
        finally:
            parent.close()
            child.close()
            if process.is_alive():
                process.kill()
                process.join()
                runtime.emergency_stop()
