import json
import os
import time
import urllib.request
from providers.base import LLMProvider, LLMResponse
from runtime.errors import PolicyGenerationError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY") or ""
        self.model = model or os.getenv("LLM_MODEL") or ""
        if not self.base_url or not self.model:
            raise ValueError("LLM_BASE_URL and LLM_MODEL are required")

    def generate_policy(self, task: str, robot_api: str, world_state: str) -> LLMResponse:
        from agent.prompts import SYSTEM_PROMPT
        body = json.dumps({"model": self.model, "stream": False, "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {task}\nRobot API:\n{robot_api}\nWorld state:\n{world_state}"},
        ]}).encode("utf-8")
        request = urllib.request.Request(self.base_url + "/chat/completions", body,
                                         {"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key})
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            content = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage") or {}
            return LLMResponse(content, payload.get("model") or self.model,
                               usage.get("prompt_tokens"), usage.get("completion_tokens"), None,
                               (time.perf_counter() - start) * 1000)
        except Exception as exc:
            raise PolicyGenerationError(f"LLM request failed: {exc}") from exc
