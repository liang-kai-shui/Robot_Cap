"""Run existing benchmark suites with explicit vendor thinking controls.

Credentials are read from BENCH_API_KEY, then removed from the environment before
policy workers spawn. No credentials or reasoning text are persisted.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from providers.base import LLMResponse
from providers.openai_compatible import OpenAICompatibleProvider
from runtime.errors import PolicyGenerationError, PolicyGenerationTimeoutError


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class MeasuredProvider(OpenAICompatibleProvider):
    settings = {}

    def __init__(self, **kwargs):
        super().__init__(base_url=self.settings["base_url"],
                         api_key=self.settings["api_key"], model=self.settings["model"],
                         stream=True, request_timeout_seconds=30, reasoning_effort="none")

    def generate_policy(self, task, robot_api, world_state, system_prompt=None):
        from agent.prompts import SYSTEM_PROMPT
        payload = {"model": self.model, "stream": True,
                   "stream_options": {"include_usage": True},
                   "temperature": 0, "max_tokens": 8192,
                   "messages": [
                       {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
                       {"role": "user", "content":
                        f"Task: {task}\nRobot API:\n{robot_api}\nWorld state:\n{world_state}"}]}
        payload.update(self.settings["thinking_control"])
        start = time.perf_counter()
        record = {"started_utc": datetime.now(timezone.utc).isoformat(),
                  "requested_model": self.model, "task": task,
                  "prompt_sha256": hashlib.sha256(json.dumps(payload["messages"]).encode()).hexdigest(),
                  "usage": {}, "reasoning_content_present": False}
        try:
            request = urllib.request.Request(self.base_url + "/chat/completions",
                json.dumps(payload).encode("utf-8"),
                {"Content-Type": "application/json", "Authorization": "Bearer " + self.api_key})
            with urllib.request.urlopen(request, timeout=30) as response:
                record["request_id"] = response.headers.get("x-request-id")
                def measured_lines():
                    for line in response:
                        if line.startswith(b"data:"):
                            try:
                                event = json.loads(line[5:].strip())
                                if event.get("usage"):
                                    record["usage"] = event["usage"]
                                if event.get("model"):
                                    record["returned_model"] = event["model"]
                                for choice in event.get("choices") or []:
                                    if (choice.get("delta") or {}).get("reasoning_content"):
                                        record["reasoning_content_present"] = True
                                    if choice.get("finish_reason"):
                                        record["finish_reason"] = choice["finish_reason"]
                            except (ValueError, AttributeError):
                                pass
                        yield line
                result = self._read_stream(measured_lines(), start)
            record.update(success=True, ttft_ms=result.ttft_ms, total_ms=result.total_ms)
            return result
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                try:
                    error = json.loads(exc.read()).get("error", {})
                    message = f"HTTP {exc.code}: {error.get('code', '')}: {error.get('message', '')}"
                except (ValueError, AttributeError, OSError):
                    message = f"HTTP {exc.code}"
            else:
                message = str(exc)
            message = message.replace(self.api_key, "[redacted]")[:1000]
            reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
            kind = PolicyGenerationTimeoutError if isinstance(reason, TimeoutError) else PolicyGenerationError
            record.update(success=False, error_type=kind.__name__, error=message,
                          total_ms=(time.perf_counter() - start) * 1000)
            raise kind(message) from exc
        finally:
            with Path(self.settings["call_log"]).open("a", encoding="utf-8") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")


def invoke(module_name, args):
    module = importlib.import_module(module_name)
    module.OpenAICompatibleProvider = MeasuredProvider
    sys.argv = [module_name + ".py", *args]
    module.main()


def model_run(args):
    key = os.environ.pop("BENCH_API_KEY", "")
    if not key:
        raise SystemExit("BENCH_API_KEY required")
    model = "qwen3.7-flash" if args.vendor == "qwen" else "deepseek-flash"
    url = os.getenv("BENCH_BASE_URL") or (
        "https://dashscope.aliyuncs.com/compatible-mode/v1" if args.vendor == "qwen"
        else "https://api.deepseek.com")
    thinking = {"enable_thinking": False} if args.vendor == "qwen" else {"thinking": {"type": "disabled"}}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"vendor": args.vendor, "model": model, "base_url": url,
                "thinking_control": thinking, "stream": True, "temperature": 0,
                "max_tokens": 8192, "socket_read_timeout_seconds": 30,
                "repeats": 3, "started_utc": datetime.now(timezone.utc).isoformat(),
                "python": sys.version, "source_sha256": {
                    str(p.relative_to(ROOT)).replace('\\', '/'):
                    hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(ROOT.rglob("*.py"))
                    if not any(part.startswith('.') or part in ("runs", "build", "__pycache__")
                               for part in p.relative_to(ROOT).parts)}}
    save(output / "comparison_manifest.json", manifest)
    MeasuredProvider.settings = dict(api_key=key, model=model, base_url=url,
                                    thinking_control=thinking, call_log=str(output / "preflight_calls.jsonl"))
    try:
        response = MeasuredProvider().generate_policy("Reply only OK", "", "", "Reply only OK")
        save(output / "preflight.json", {"success": True, "model": response.model,
                                        "text": response.text, "total_ms": response.total_ms})
        print(f"{args.vendor}: preflight OK, returned model={response.model}", flush=True)
    except PolicyGenerationError as exc:
        save(output / "preflight.json", {"success": False, "error": str(exc)})
        raise SystemExit(str(exc))
    if args.stage == "preflight":
        return
    suites = [("base", "benchmark", ["--stream"]),
              ("complex", "complex_benchmark", ["--stream"]),
              ("interactive", "interactive_benchmark", ["--stream", "--reasoning-effort", "none"]),
              ("mission-hybrid", "mission_benchmark", ["--strategy", "hybrid", "--reasoning-effort", "none"]),
              ("mission-direct", "mission_benchmark", ["--strategy", "direct", "--reasoning-effort", "none"])]
    for name, module, extra in suites:
        destination = output / name
        if (destination / "summary.json").exists():
            raise SystemExit(f"Refusing to overwrite existing suite {destination}")
        destination.mkdir(parents=True, exist_ok=True)
        MeasuredProvider.settings["call_log"] = str(destination / "api_calls.jsonl")
        print(f"{args.vendor}: starting {name}", flush=True)
        invoke(module, ["--provider", "openai-compatible", "--model", model,
                        "--runs", "3", "--output", str(destination), *extra])
        print(f"{args.vendor}: completed {name}", flush=True)
    save(output / "completed.json", {"completed_utc": datetime.now(timezone.utc).isoformat()})


def controls_run(args):
    output = Path(args.output)
    suites = [
        ("mock", "benchmark", ["--provider", "mock"]),
        ("complex-reference", "complex_benchmark", ["--provider", "reference"]),
        ("interactive-reference", "interactive_benchmark", ["--provider", "reference"]),
        ("navigation-fixed", "navigation_benchmark", []),
        ("navigation-random", "navigation_benchmark", ["--random-worlds", "20", "--seed", "20261006"]),
        ("mission-reference", "mission_benchmark", ["--provider", "reference", "--strategy", "reference", "--runs", "3"]),
        ("robustness-fixed", "robustness_benchmark", ["--runs", "3", "--seed", "20261006"]),
        ("robustness-random", "robustness_benchmark", ["--random-worlds", "30", "--world-seed", "161803", "--seed", "20261007", "--profile", "ideal", "--profile", "range-3cm", "--profile", "move-8pct", "--profile", "odometry-3pct", "--profile", "combined"])]
    for name, module, extra in suites:
        print(f"Controls: starting {name}", flush=True)
        result = subprocess.run([sys.executable, "-u", str(ROOT / (module + ".py")),
                                 "--output", str(output / name), *extra], cwd=ROOT)
        if result.returncode:
            raise SystemExit(result.returncode)
    save(output / "completed.json", {"completed_utc": datetime.now(timezone.utc).isoformat()})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", choices=("qwen", "deepseek", "controls"), required=True)
    parser.add_argument("--stage", choices=("preflight", "full"), default="full")
    parser.add_argument("--output", required=True)
    options = parser.parse_args()
    controls_run(options) if options.vendor == "controls" else model_run(options)
