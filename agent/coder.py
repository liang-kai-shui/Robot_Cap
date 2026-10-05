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

    def generate_from_observation(self, instruction: str, observation: dict,
                                  registry: CapabilityRegistry | None = None) -> tuple[str, LLMResponse]:
        """Generate one short policy from local observations, without a map dump."""
        registry = registry if registry is not None else DEFAULT_REGISTRY
        context = json.dumps({"observation_only": True, "current": observation}, ensure_ascii=False)
        system = ("You are a robot planning agent. Select the next action toward the task goal "
                  "using ONLY the provided Robot API, current observation and recent steps. "
                  "Return exactly ONE direct registered action call with literal arguments. "
                  "No Markdown, explanations, imports, variables, expressions, conditions, loops, "
                  "or observation calls. Never output multiple calls. "
                  "After every action, including a turn, a fresh observation will be provided. "
                  "Use recent steps to continue a detour and avoid undoing turns repeatedly. "
                  "Choose short detours and reassess toward the goal rather than moving farther away unnecessarily. "
                  "Only forward moves are guarded; turn and observe before moving in another direction. "
                  "A move must not exceed navigation_limits.safe_forward_distance_m. "
                  "Do not assume unseen space is free. Finite actions return stopped; no extra stop is needed. "
                  "A stop far from the goal does not complete the task. "
                  "Coordinates: heading 0 points +x, 90 points +y; positive turns are counterclockwise.")
        response = self.provider.generate_policy(
            instruction, generate_robot_api_prompt(registry), context, system)
        return extract_policy(response.text), response
