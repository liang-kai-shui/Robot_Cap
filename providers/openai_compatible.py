import json
import os
import time
import urllib.request
from providers.base import LLMProvider, LLMResponse
from runtime.errors import PolicyGenerationError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None,
                 stream: bool = False, include_stream_usage: bool = True):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY") or ""
        self.model = model or os.getenv("LLM_MODEL") or ""
        self.stream = stream
        self.include_stream_usage = include_stream_usage
        if not self.base_url or not self.model:
            raise ValueError("LLM_BASE_URL and LLM_MODEL are required")

    def generate_policy(self, task: str, robot_api: str, world_state: str) -> LLMResponse:
        from agent.prompts import SYSTEM_PROMPT
        payload = {"model": self.model, "stream": self.stream, "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {task}\nRobot API:\n{robot_api}\nWorld state:\n{world_state}"},
        ]}
        if self.stream and self.include_stream_usage:
            payload["stream_options"] = {"include_usage": True}
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base_url + "/chat/completions", body,
                                         {"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key})
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if self.stream:
                    return self._read_stream(response, start)
                result = json.load(response)
            content = result["choices"][0]["message"]["content"]
            usage = result.get("usage") or {}
            return LLMResponse(content, result.get("model") or self.model,
                               usage.get("prompt_tokens"), usage.get("completion_tokens"), None,
                               (time.perf_counter() - start) * 1000)
        except Exception as exc:
            raise PolicyGenerationError(f"LLM request failed: {exc}") from exc

    def _read_stream(self, response, start: float) -> LLMResponse:
        chunks = []
        model = self.model
        usage = {}
        ttft_ms = None
        done = False
        data_lines = []

        def process_event(data: str) -> bool:
            nonlocal model, usage, ttft_ms
            if data == "[DONE]":
                return True
            event = json.loads(data)
            model = event.get("model") or model
            usage = event.get("usage") or usage
            for choice in event.get("choices") or []:
                content = (choice.get("delta") or {}).get("content")
                if isinstance(content, str) and content:
                    if ttft_ms is None:
                        ttft_ms = (time.perf_counter() - start) * 1000
                    chunks.append(content)
            return False

        for raw_line in response:
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif not line and data_lines:
                done = process_event("\n".join(data_lines))
                data_lines = []
                if done:
                    break
        if data_lines and not done:
            done = process_event("\n".join(data_lines))
        if not done or not chunks:
            raise PolicyGenerationError("Incomplete or empty streaming policy response")
        return LLMResponse("".join(chunks), model, usage.get("prompt_tokens"),
                           usage.get("completion_tokens"), ttft_ms,
                           (time.perf_counter() - start) * 1000)
