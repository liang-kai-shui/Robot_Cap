import json
import re
import time
from dataclasses import asdict
from robot.state import RobotState
from runtime.errors import PolicyGenerationError
from agent.prompts import generate_robot_api_prompt, generate_system_prompt
from robot.capabilities import CapabilityRegistry, DEFAULT_REGISTRY
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

    def generate(self, task: Task, registry: CapabilityRegistry | None = None) -> tuple[str, LLMResponse]:
        """Generate policy against exactly the registry exposed to execution."""
        registry = registry if registry is not None else DEFAULT_REGISTRY
        world_state = json.dumps({"initial": asdict(task.initial), "world": asdict(task.world)}, ensure_ascii=False)
        response = self.provider.generate_policy(task.instruction, generate_robot_api_prompt(registry),
                                                 world_state, generate_system_prompt(registry))
        return extract_policy(response.text), response
