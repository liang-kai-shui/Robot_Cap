import re
import time
from providers.base import LLMProvider, LLMResponse
from runtime.errors import PolicyGenerationError


class MockProvider(LLMProvider):
    def generate_policy(self, task: str, robot_api: str, world_state: str) -> LLMResponse:
        start = time.perf_counter()
        from task.benchmark_tasks import BENCHMARK_TASKS, MOCK_POLICIES
        policy = next((MOCK_POLICIES[item.id] for item in BENCHMARK_TASKS if item.instruction == task and item.id in MOCK_POLICIES), None)
        if policy is None:
            normalized = re.sub(r"[\s。.]", "", task)
            simple = {"向前走1米": "robot.move(1.0)\nrobot.stop()", "向前走2米": "robot.move(2.0)\nrobot.stop()",
                      "左转90度": "robot.turn(90)\nrobot.stop()"}
            policy = simple.get(normalized)
        if policy is None:
            raise PolicyGenerationError("MockProvider only supports fixed benchmark and example instructions")
        return LLMResponse(policy, "mock", None, None, None, (time.perf_counter()-start)*1000)
