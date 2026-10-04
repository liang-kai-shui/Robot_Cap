from dataclasses import dataclass
from providers.base import LLMResponse


@dataclass(frozen=True)
class GeneratedPolicy:
    code: str
    response: LLMResponse
