"""Parent-owned sandbox lifecycle and robot command service."""
import json
import multiprocessing as mp
import time
from dataclasses import dataclass, field
from robot.backends.virtual import VirtualBackend
from robot.capabilities import CapabilityRegistry
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
    api_surface_signature: list[str] = field(default_factory=list)


class PolicyExecutor:
    """Spawn a policy worker while retaining backend ownership in the parent."""

    def __init__(self, limits: RuntimeLimits | None = None):
        self.limits = limits or RuntimeLimits()

    def execute(self, policy: str, world: VirtualWorld, initial: Pose, validated: bool = False,
                backend=None, registry: CapabilityRegistry | None = None,
                runtime: RobotRuntime | None = None) -> WorkerOutcome:
        """Validate and run one policy; validated is a legacy, non-bypass hint."""
        runtime = runtime if runtime is not None else RobotRuntime(
            backend or VirtualBackend(world, initial, self.limits), self.limits, registry)
        # Validation is a trust boundary, so a caller's validated hint cannot skip it.
        PolicyValidator(runtime.registry).validate(policy)
        trace_start = len(runtime.trace)
        logs_start = len(getattr(runtime.backend, "logs", []))
        context = mp.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(target=run_worker,
                                  args=(policy, child, runtime.registry.manifest(),
                                        self.limits.communication_timeout_seconds,
                                        runtime.next_command_id))
        start = time.perf_counter()
        success, error_type, message = False, "PolicyExecutionError", "Worker exited without result"
        emergency = False
        try:
            process.start()
            child.close()
            compute_remaining = self.limits.effective_policy_timeout_seconds
            compute_started = time.perf_counter()
            startup_deadline = compute_started + max(5.0, self.limits.communication_timeout_seconds)
            ready = False
            while True:
                remaining = ((compute_remaining - (time.perf_counter() - compute_started))
                             if ready else startup_deadline - time.perf_counter())
                if remaining <= 0:
                    if ready:
                        error_type, message = "PolicyTimeoutError", "Policy exceeded compute timeout"
                    else:
                        error_type, message = "PolicyExecutionError", "Worker startup timed out"
                    emergency = True
                    break
                if parent.poll(min(remaining, 0.05)):
                    try:
                        packet = json.loads(parent.recv_bytes().decode("utf-8"))
                    except (EOFError, OSError, ValueError):
                        emergency = True
                        break
                    if packet.get("kind") == "ready":
                        ready = True
                        compute_started = time.perf_counter()
                        continue
                    if packet.get("kind") == "outcome":
                        success = bool(packet.get("success"))
                        error_type, message = packet.get("error_type"), packet.get("error_message")
                        break
                    if ready and packet.get("kind") == "command":
                        accepted = runtime.accepts(packet)
                        if accepted:
                            compute_remaining = max(0.0, compute_remaining - (time.perf_counter() - compute_started))
                            acknowledgement = {"kind": "accepted", "command_id": packet.get("command_id"),
                                               "action_timeout_seconds": runtime.action_timeout(packet)}
                            try:
                                parent.send_bytes(json.dumps(acknowledgement).encode("utf-8"))
                            except (EOFError, OSError):
                                emergency = True
                                break
                        response = runtime.dispatch(packet)
                        response["command_id"] = packet.get("command_id")
                        response["kind"] = "result"
                        try:
                            parent.send_bytes(json.dumps(response).encode("utf-8"))
                        except (EOFError, OSError):
                            emergency = True
                            break
                        if accepted:
                            compute_started = time.perf_counter()
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
            return WorkerOutcome(success, error_type, message, runtime.snapshot() or RobotState(initial),
                                 list(logs[logs_start:]), (time.perf_counter() - start) * 1000,
                                 list(runtime.trace[trace_start:]),
                                 runtime.registry.api_surface_signature())
        finally:
            parent.close()
            child.close()
            if process.is_alive():
                process.kill()
                process.join()
                runtime.emergency_stop()
