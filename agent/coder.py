import json
import re
import time
from dataclasses import asdict
from robot.state import RobotState
from runtime.errors import PolicyGenerationError
from agent.prompts import ROBOT_API
from providers.base import LLMProvider, LLMResponse
from task.task import Task


def extract_policy(text: str) -> str:
    value = text.strip()
    if "```" in value:
        matches = re.findall(r"```(?:python)?[ \t]*\n(.*?)\n```", value, re.DOTALL | re.IGNORECASE)
        if len(matches) != 1:
            raise PolicyGenerationError("Cannot unambiguously extract one Python code block")
        value = matches[0].strip()
    if not value:
        raise PolicyGenerationError("Empty policy response")
    return value


class AgentCoder:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def generate(self, task: Task) -> tuple[str, LLMResponse]:
        world_state = json.dumps({"initial": asdict(task.initial), "world": asdict(task.world)}, ensure_ascii=False)
        response = self.provider.generate_policy(task.instruction, ROBOT_API, world_state)
        return extract_policy(response.text), response
