from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    ttft_ms: float | None
    total_ms: float


class LLMProvider(ABC):
    @abstractmethod
    def generate_policy(self, task: str, robot_api: str, world_state: str,
                        system_prompt: str | None = None) -> LLMResponse: ...
