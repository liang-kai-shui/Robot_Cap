import multiprocessing as mp
import time
from dataclasses import dataclass
from robot.state import Pose, RobotState
from runtime.limits import RuntimeLimits
from runtime.validator import PolicyValidator
from runtime.worker import run_worker
from world.world import VirtualWorld


@dataclass
class WorkerOutcome:
    success: bool
    error_type: str | None
    error_message: str | None
    final_state: RobotState
    logs: list[dict]
    execution_ms: float


class PolicyExecutor:
    def __init__(self, limits: RuntimeLimits | None = None):
        self.limits = limits or RuntimeLimits()

    def execute(self, policy: str, world: VirtualWorld, initial: Pose, validated: bool = False) -> WorkerOutcome:
        if not validated:
            PolicyValidator().validate(policy)
        initial_state = RobotState(Pose(initial.x, initial.y, initial.heading % 360))
        context = mp.get_context("spawn")
        receiving, sending = context.Pipe(duplex=False)
        process = context.Process(target=run_worker, args=(policy, world, initial, self.limits, sending))
        start = time.perf_counter()
        try:
            process.start()
            sending.close()
            remaining = max(0.0, self.limits.timeout_seconds - (time.perf_counter() - start))
            if receiving.poll(remaining):
                try:
                    success, error_type, message, state, logs = receiving.recv()
                except EOFError:
                    success, error_type, message, state, logs = False, "PolicyExecutionError", "Worker exited without result", None, []
                process.join(timeout=0.05)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=0.2)
                return WorkerOutcome(success, error_type, message, state or initial_state, logs, (time.perf_counter()-start)*1000)
            process.terminate()
            process.join(timeout=0.5)
            if process.is_alive():
                process.kill()
                process.join(timeout=0.5)
            return WorkerOutcome(False, "PolicyTimeoutError", "Policy exceeded execution timeout", initial_state, [], (time.perf_counter()-start)*1000)
        finally:
            receiving.close()
            if process.is_alive():
                process.kill()
                process.join()
