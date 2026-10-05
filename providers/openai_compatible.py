import json
import os
import math
import urllib.error
import time
import urllib.request
from providers.base import LLMProvider, LLMResponse
from runtime.errors import PolicyGenerationError, PolicyGenerationTimeoutError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, base_url: str | None = None, api_key: str | None = None, model: str | None = None,
                 stream: bool = False, include_stream_usage: bool = True,
                 request_timeout_seconds: float | None = None, reasoning_effort: str | None = None):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY") or ""
        self.model = model or os.getenv("LLM_MODEL") or ""
        self.stream = stream
        self.include_stream_usage = include_stream_usage
        self.request_timeout_seconds = float(request_timeout_seconds if request_timeout_seconds is not None
                                             else os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "30"))
        if not math.isfinite(self.request_timeout_seconds) or self.request_timeout_seconds <= 0:
            raise ValueError("LLM request timeout must be positive and finite")
        self.reasoning_effort = reasoning_effort or os.getenv("LLM_REASONING_EFFORT") or None
        if self.reasoning_effort not in (None, "none", "low", "medium", "high", "xhigh", "max"):
            raise ValueError("Invalid LLM reasoning effort")
        if not self.base_url or not self.model:
            raise ValueError("LLM_BASE_URL and LLM_MODEL are required")

    def generate_policy(self, task: str, robot_api: str, world_state: str,
                        system_prompt: str | None = None) -> LLMResponse:
        from agent.prompts import SYSTEM_PROMPT
        payload = {"model": self.model, "stream": self.stream, "messages": [
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {task}\nRobot API:\n{robot_api}\nWorld state:\n{world_state}"},
        ]}
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.stream and self.include_stream_usage:
            payload["stream_options"] = {"include_usage": True}
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base_url + "/chat/completions", body,
                                         {"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key})
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout_seconds) as response:
                if self.stream:
                    return self._read_stream(response, start)
                result = json.load(response)
            content = result["choices"][0]["message"]["content"]
            usage = result.get("usage") or {}
            return LLMResponse(content, result.get("model") or self.model,
                               usage.get("prompt_tokens"), usage.get("completion_tokens"), None,
                               (time.perf_counter() - start) * 1000)
        except urllib.error.HTTPError as exc:
            try:
                details = json.loads(exc.read()).get("error", {})
                message = f"{details.get('code', '')}: {details.get('message', '')}"
            except (ValueError, AttributeError, OSError):
                message = str(exc)
            if self.api_key:
                message = message.replace(self.api_key, "[redacted]")
            raise PolicyGenerationError(f"LLM HTTP {exc.code}: {message[:1000]}") from exc
        except Exception as exc:
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            message = str(reason)
            if self.api_key:
                message = message.replace(self.api_key, "[redacted]")
            if isinstance(reason, TimeoutError):
                raise PolicyGenerationTimeoutError(
                    f"LLM socket/read timeout ({self.request_timeout_seconds:g} seconds): {message}") from exc
            raise PolicyGenerationError(f"LLM request failed: {message}") from exc

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
