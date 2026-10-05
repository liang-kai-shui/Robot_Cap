import io
import json
import pytest
from providers.openai_compatible import OpenAICompatibleProvider
from runtime.errors import PolicyGenerationError


def test_non_stream_response(monkeypatch):
    request_bodies = []
    def urlopen(request, timeout):
        request_bodies.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"model": "served", "choices": [{"message": {"content": "robot.stop()"}}],
                                   "usage": {"prompt_tokens": 10, "completion_tokens": 4}}).encode())
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    response = OpenAICompatibleProvider("https://example.test/v1", "key", "requested").generate_policy("task", "api", "world")
    assert request_bodies[0]["stream"] is False
    assert response.text == "robot.stop()" and response.model == "served"
    assert response.input_tokens == 10 and response.output_tokens == 4
    assert response.ttft_ms is None


def test_stream_ttft_and_usage(monkeypatch):
    request_bodies = []
    def event(payload):
        return ("data: " + json.dumps(payload) + "\n\n").encode()
    stream = b"".join([
        event({"model": "served", "choices": [{"delta": {"role": "assistant"}}]}),
        event({"choices": [{"delta": {"content": "robot."}}]}),
        event({"choices": [{"delta": {"content": "stop()"}}]}),
        event({"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 5}}),
        b"data: [DONE]\n\n",
    ])
    def urlopen(request, timeout):
        request_bodies.append(json.loads(request.data))
        return io.BytesIO(stream)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    response = OpenAICompatibleProvider("https://example.test/v1", "key", "requested", stream=True).generate_policy("task", "api", "world")
    assert request_bodies[0]["stream_options"] == {"include_usage": True}
    assert response.text == "robot.stop()" and response.model == "served"
    assert response.ttft_ms is not None and 0 <= response.ttft_ms <= response.total_ms
    assert response.input_tokens == 11 and response.output_tokens == 5


def test_stream_without_usage_and_incomplete_stream(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: io.BytesIO(
        b'data: {"choices":[{"delta":{"content":"robot.stop()"}}]}\n\ndata: [DONE]\n\n'))
    response = OpenAICompatibleProvider("https://example.test/v1", "key", "model", stream=True,
                                        include_stream_usage=False).generate_policy("task", "api", "world")
    assert response.input_tokens is None and response.output_tokens is None
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: io.BytesIO(
        b'data: {"choices":[{"delta":{"content":"robot.stop()"}}]}\n\n'))
    with pytest.raises(PolicyGenerationError):
        OpenAICompatibleProvider("https://example.test/v1", "key", "model", stream=True).generate_policy("task", "api", "world")


def test_request_timeout_and_reasoning_options(monkeypatch):
    def urlopen(request, timeout):
        assert timeout == .2
        assert json.loads(request.data)["reasoning_effort"] == "none"
        return io.BytesIO(b'{"choices":[{"message":{"content":"robot.stop()"}}]}')
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    OpenAICompatibleProvider("https://example.test", "key", "model", request_timeout_seconds=.2,
                             reasoning_effort="none").generate_policy("task", "api", "world")


def test_timeout_is_distinct_from_generation_errors(monkeypatch):
    import urllib.error
    from runtime.errors import PolicyGenerationTimeoutError
    def urlopen(request, timeout):
        raise urllib.error.URLError(TimeoutError("socket timed out"))
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(PolicyGenerationTimeoutError, match="socket/read timeout"):
        OpenAICompatibleProvider("https://example.test", "key", "model").generate_policy("task", "api", "world")


def test_http_error_preserves_reason_without_key(monkeypatch):
    import urllib.error
    key = "private-test-key"
    def urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "quota", {}, io.BytesIO(json.dumps(
            {"error": {"code": "quota_exhausted", "message": "limit for " + key}}).encode()))
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(PolicyGenerationError, match="quota_exhausted") as caught:
        OpenAICompatibleProvider("https://example.test", key, "model").generate_policy("task", "api", "world")
    assert key not in str(caught.value)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_request_timeout(timeout):
    with pytest.raises(ValueError):
        OpenAICompatibleProvider("https://example.test", "key", "model", request_timeout_seconds=timeout)
