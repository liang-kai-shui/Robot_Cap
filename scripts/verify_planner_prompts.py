"""Frozen prompt ablation; production prompts, tasks and validators stay unchanged.

Run --prepare first, then --vendor qwen/deepseek with BENCH_API_KEY in the
environment. --analyze validates every raw reply with the production parser and
replays unique existing-task replies through the real hybrid navigation runner.
No retries, response repair, hidden map input or evaluator answers are sent.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import statistics
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.mission import MissionRunner
from agent.task_planner import GoalPlan, TaskPlanner
from main import load_config
from navigation.planner import NavigationGoal
from providers.base import LLMResponse
from providers.openai_compatible import OpenAICompatibleProvider
from runtime.errors import PolicyGenerationError
from task.mission_benchmark_tasks import MISSION_SCENARIOS
from task.waypoint_evaluator import evaluate_waypoints

ARMS = ("baseline", "shared", "native_profile", "cross_profile", "shared_json")
SHARED = (
    'You interpret mission destinations; a trusted navigation session observes the world, '
    'plans collision-checked motion and stops at each destination. You generate no motion code. '
    'Return exactly one JSON object, with exactly one of these forms: '
    '{"targets":["catalogue_key",...]} or {"stop_reason":"reason"}. '
    'targets must contain 1 to 8 strings, each an EXACT KEY of public_targets. '
    'Values in public_targets describe coordinates; use them to match coordinate requests '
    'to existing keys, never invent IDs or return coordinate objects. '
    'Include all and only requested visits, in order, including explicit returns and repeated visits. '
    'feedback.completed_targets is the already executed prefix of the original mission. '
    'Remove only that completed prefix when replanning; keep later requested revisits. '
    'Use stop_reason (1 to 200 characters) if a requested destination cannot be resolved '
    'from the catalogue or feedback reports an actual safety condition preventing continuation. '
    'A stop is an abort, not successful mission completion. '
    'An initially empty observed map means unobserved, not blocked or unsafe by itself. '
    'The trusted navigator obtains observations before moving. Exhausted local information '
    'does not establish global unreachability; keep unresolved requested destinations unless '
    'there is explicit evidence requiring an abort. '
    'No additional keys, markdown, explanations, Python, hidden map assumptions or unrequested targets.'
)
PROFILES = {
    "qwen": ('Serialization checklist: targets elements are quoted catalogue keys, never objects. '
             'A key such as "place_17" must be copied exactly, even for a coordinate-only request. '
             'Do not rename it to "target_0" or a coordinate-derived name. '
             'Do not deduplicate repeated visits. Output only the final JSON.'),
    "deepseek": ('Resolve the requested sequence against catalogue keys, subtract only the '
                 'completed prefix, and serialize that sequence directly. '
                 'Use the exact JSON shape and keep any abort reason within 200 characters.'),
}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class CaptureProvider:
    def generate_policy(self, task, robot_api, world_state, system_prompt=None):
        self.messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content":
             f"Task: {task}\nRobot API:\n{robot_api}\nWorld state:\n{world_state}"}]
        return LLMResponse("", "capture", None, None, None, 0)


def variants(messages, catalogue, vendor):
    example = json.dumps({"targets": [next(iter(catalogue))]}, ensure_ascii=False)
    common = SHARED + (' Generic serialization example for a request visiting ONLY the '
                       'first catalogue key (this is not an answer to the actual mission): ' + example + '.')
    result = {}
    for arm in ARMS:
        system = messages[0]["content"] if arm == "baseline" else common
        if arm in ("native_profile", "cross_profile"):
            profile = vendor if arm == "native_profile" else ("deepseek" if vendor == "qwen" else "qwen")
            system += " " + PROFILES[profile]
        result[arm] = [{"role": "system", "content": system}, messages[1]]
    return result


def prepare(output):
    destination = output / "manifest.json"
    if destination.exists():
        raise SystemExit("Refusing to overwrite frozen experiment")
    _, _, limits = load_config(str(ROOT / "config/default.yaml"))
    runner = MissionRunner(limits)
    cases = []
    for item in MISSION_SCENARIOS:
        session = runner.navigation.session(item.task, next(iter(item.public_targets.values())))
        session.initialize()
        session.runtime.emergency_stop()
        feedback = {**session.public_context(), "completed_targets": [],
                    "trigger": "mission-start", "execution_error": None}
        capture = CaptureProvider()
        TaskPlanner(capture).request(item.task.instruction, item.public_targets, feedback, session.runtime.registry)
        cases.append({"id": item.task.id, "split": "existing", "repeats": 3,
                      "messages": capture.messages,
                      "catalogue": {key: asdict(goal) for key, goal in item.public_targets.items()},
                      "expected": {"targets": list(item.expected_order)}})
    # New cases are frozen before API calls. The expectations never enter messages.
    holdouts = [
        ("U1", "到达(2.6,1.4)，停稳。地图未知。", {"dock_K7": (2.6, 1.4)}, ("dock_K7",), []),
        ("U2", "依次去充电位、窗边，再去一次充电位。", {"充电位": (2.2, 1), "窗边": (3, 2)},
         ("充电位", "窗边", "充电位"), []),
        ("U3", "直接去(-1.2,0.8)，不要访问另外两个点。", {"p-9": (-1.2, .8), "p-8": (1, 2), "p-7": (2, 3)},
         ("p-9",), []),
        ("U4", "先去node_x，再去node_y，最后再次去node_x。", {"node_x": (2, 1), "node_y": (3, 2)},
         ("node_y", "node_x"), ["node_x"]),
        ("U5", "先去station_C，再返回出发点。", {"station_C": (2, 4), "origin_Z": (5, 3), "unused": (1, 1)},
         ("station_C", "origin_Z"), []),
        ("U6", "Visit bay_blue, then bay_red; return to bay_blue, then finish at bay_green.",
         {"bay_green": (4, 3), "bay_red": (3, 2), "bay_blue": (2, 1)},
         ("bay_red", "bay_blue", "bay_green"), ["bay_blue"]),
        ("U7", "去目录中没有提供位置的仓库Q，不要把其他地点当作仓库Q。", {"desk_R": (2, 1)}, (), []),
        ("U8", "控制器报告急停已锁定。停止本次任务，不得继续前往port_N。", {"port_N": (2, 1)}, (), []),
    ]
    template = cases[0]
    template_context = json.loads(template["messages"][1]["content"].split("World state:\n", 1)[1])
    api = template["messages"][1]["content"].split("Robot API:\n", 1)[1].split("\nWorld state:\n", 1)[0]
    for identifier, instruction, points, expected, prefix in holdouts:
        catalogue = {key: asdict(NavigationGoal(*point)) for key, point in points.items()}
        feedback = {**template_context["feedback"], "completed_targets": prefix}
        if prefix:
            feedback.update(trigger="NavigationStalled", remaining_actions=60, remaining_observations=120)
        if identifier == "U5":
            feedback["pose_estimate"] = {"x": 5, "y": 3, "heading": 180}
        if identifier == "U8":
            feedback["execution_error"] = "EmergencyStopLatched"
        context = json.dumps({"public_targets": catalogue, "feedback": feedback}, ensure_ascii=False)
        messages = [{"role": "system", "content": template["messages"][0]["content"]},
                    {"role": "user", "content": f"Task: {instruction}\nRobot API:\n{api}\nWorld state:\n{context}"}]
        cases.append({"id": identifier, "split": "unseen", "repeats": 2, "messages": messages,
                      "catalogue": catalogue, "expected": {"targets": list(expected)} if expected else {"abort": True}})
    frozen = {"experiment": "planner-prompt-ablation-v1", "created_utc": datetime.now(timezone.utc).isoformat(),
              "arms": list(ARMS), "shared_prompt": SHARED, "profiles": PROFILES, "cases": cases,
              "temperature": 0, "max_tokens": 8192, "thinking": False, "stream": True,
              "concurrency_per_vendor": 2, "retries": 0, "seed": 20261007,
              "sources": {str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
                          for folder in ("agent", "robot", "runtime", "navigation", "task")
                          for path in sorted((ROOT / folder).rglob("*.py"))},
              "runtime_limits": asdict(limits),
              "note": "Only system wording and declared JSON mode vary; user context, parser and scorer are fixed. "
                      "New cases were designed after inspecting earlier failures, before this experiment, without iterative tuning."}
    for case in cases:
        if case["expected"].get("targets"):
            GoalPlan.parse(json.dumps(case["expected"]), case["catalogue"])
        for vendor in ("qwen", "deepseek"):
            case[vendor + "_messages"] = variants(case["messages"], case["catalogue"], vendor)
    save(destination, frozen)
    print(f"Frozen {len(cases)} cases, {sum(c['repeats'] for c in cases)*len(ARMS)} requests/vendor", flush=True)


def trial(key, url, model, vendor, case, arm, repeat, manifest_hash):
    messages = case[vendor + "_messages"][arm]
    payload = {"model": model, "stream": True, "stream_options": {"include_usage": True},
               "temperature": 0, "max_tokens": 8192, "messages": messages}
    payload.update({"enable_thinking": False} if vendor == "qwen" else {"thinking": {"type": "disabled"}})
    if arm == "shared_json":
        payload["response_format"] = {"type": "json_object"}
    record = {"vendor": vendor, "requested_model": model, "case_id": case["id"], "split": case["split"],
              "arm": arm, "repeat": repeat, "manifest_sha256": manifest_hash,
              "prompt_sha256": hashlib.sha256(json.dumps(messages).encode()).hexdigest(),
              "response_format": payload.get("response_format"), "started_utc": datetime.now(timezone.utc).isoformat(),
              "usage": {}, "reasoning_content_present": False, "response": None}
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url + "/chat/completions", json.dumps(payload).encode(),
                    {"Content-Type": "application/json", "Authorization": "Bearer " + key})
        reader = OpenAICompatibleProvider(base_url=url, api_key=key, model=model, stream=True)
        with urllib.request.urlopen(request, timeout=30) as response:
            record["request_id"] = response.headers.get("x-request-id")
            def lines():
                for line in response:
                    if line.startswith(b"data:"):
                        try:
                            event = json.loads(line[5:].strip())
                            if event.get("usage"):
                                record["usage"] = event["usage"]
                            if event.get("system_fingerprint"):
                                record["system_fingerprint"] = event["system_fingerprint"]
                            for choice in event.get("choices") or []:
                                if (choice.get("delta") or {}).get("reasoning_content"):
                                    record["reasoning_content_present"] = True
                                if choice.get("finish_reason"):
                                    record["finish_reason"] = choice["finish_reason"]
                        except (ValueError, AttributeError):
                            pass
                    yield line
            result = reader._read_stream(lines(), started)
        record.update(transport_success=True, returned_model=result.model, response=result.text,
                      ttft_ms=result.ttft_ms, total_ms=result.total_ms)
        try:
            plan = GoalPlan.parse(result.text, case["catalogue"])
            correct = (plan.stop_reason is not None if case["expected"].get("abort") else
                       plan.stop_reason is None and list(plan.targets) == case["expected"]["targets"])
            record.update(contract_accepted=True, plan_correct=correct, targets=list(plan.targets), stop_reason=plan.stop_reason)
        except PolicyGenerationError as exc:
            record.update(contract_accepted=False, plan_correct=False, parse_error=str(exc))
    except Exception as exc:
        if isinstance(exc, urllib.error.HTTPError):
            try:
                details = json.loads(exc.read()).get("error", {})
                message = f"HTTP {exc.code}: {details.get('code', '')}: {details.get('message', '')}"
            except (ValueError, AttributeError, OSError):
                message = f"HTTP {exc.code}"
        else:
            message = str(exc)
        record.update(transport_success=False, contract_accepted=False, plan_correct=False,
                      error_type=type(exc).__name__, error=message.replace(key, "[redacted]")[:1000],
                      total_ms=(time.perf_counter()-started)*1000)
    return record


def run(output, vendor):
    key = os.environ.pop("BENCH_API_KEY", "")
    if not key:
        raise SystemExit("BENCH_API_KEY required")
    path = output / "manifest.json"
    frozen = json.loads(path.read_text(encoding="utf-8"))
    manifest_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    destination = output / (vendor + "_calls.jsonl")
    rows = [json.loads(line) for line in destination.read_text(encoding="utf-8").splitlines()] if destination.exists() else []
    if any(row["manifest_sha256"] != manifest_hash for row in rows):
        raise SystemExit("Cannot resume changed manifest")
    finished = {(row["case_id"], row["arm"], row["repeat"]) for row in rows}
    jobs = [(case, arm, repeat) for case in frozen["cases"] for repeat in range(1, case["repeats"]+1) for arm in ARMS]
    random.Random(frozen["seed"]).shuffle(jobs)
    jobs = [job for job in jobs if (job[0]["id"], job[1], job[2]) not in finished]
    model = "qwen3.7-flash" if vendor == "qwen" else "deepseek-flash"
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1" if vendor == "qwen" else "https://api.deepseek.com"
    # Check a real JSON-mode trial first; it remains part of formal results.
    if not rows:
        index = next(i for i, job in enumerate(jobs) if job[1] == "shared_json")
        case, arm, repeat = jobs.pop(index)
        row = trial(key, url, model, vendor, case, arm, repeat, manifest_hash)
        if not row["transport_success"]:
            save(output / (vendor + "_preflight_failure.json"), row)
            raise SystemExit(row.get("error"))
        rows.append(row)
        with destination.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{vendor} JSON-mode accepted: {row['case_id']} correct={row['plan_correct']}", flush=True)
    with ThreadPoolExecutor(max_workers=frozen["concurrency_per_vendor"]) as pool:
        futures = [pool.submit(trial, key, url, model, vendor, *job, manifest_hash) for job in jobs]
        for future in as_completed(futures):
            row = future.result()
            with destination.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.append(row)
            if len(rows) % 10 == 0 or not row["transport_success"]:
                print(f"{vendor} {len(rows)}/230 last={row['case_id']}/{row['arm']} "
                      f"correct={row['plan_correct']} transport={row['transport_success']}", flush=True)
    print(f"{vendor} completed {len(rows)} calls", flush=True)


class ReplayProvider:
    def __init__(self, row):
        self.row, self.calls = row, 0

    def generate_policy(self, *args, **kwargs):
        self.calls += 1
        if self.calls != 1:
            raise PolicyGenerationError("Replay contains one recorded plan; no invented replanning response")
        return LLMResponse(self.row["response"], self.row["returned_model"], None, None, None, 0)


def analyze(output):
    frozen = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    manifest_hash = hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest()
    cases = {case["id"]: case for case in frozen["cases"]}
    rows = []
    expected_keys = {(vendor, case["id"], arm, repeat) for vendor in ("qwen", "deepseek")
                     for case in frozen["cases"] for arm in ARMS for repeat in range(1, case["repeats"]+1)}
    for vendor in ("qwen", "deepseek"):
        rows += [json.loads(line) for line in (output / (vendor + "_calls.jsonl")).read_text(encoding="utf-8").splitlines()]
    actual_keys = [(r["vendor"], r["case_id"], r["arm"], r["repeat"]) for r in rows]
    assert len(actual_keys) == len(set(actual_keys)) and set(actual_keys) == expected_keys
    assert all(r["manifest_sha256"] == manifest_hash for r in rows)
    scenarios = {item.task.id: item for item in MISSION_SCENARIOS}
    cache_path = output / "replay_index.json"
    replays = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    _, _, limits = load_config(str(ROOT / "config/default.yaml"))
    assert asdict(limits) == frozen["runtime_limits"]
    for source, digest in frozen["sources"].items():
        assert hashlib.sha256((ROOT / source).read_bytes()).hexdigest() == digest, source
    for row in rows:
        if row["split"] != "existing" or not row["transport_success"]:
            continue
        key = hashlib.sha256((row["case_id"] + "\n" + row["response"]).encode()).hexdigest()
        if key not in replays:
            item = scenarios[row["case_id"]]
            result = MissionRunner(limits).run(item.task, item.public_targets, provider=ReplayProvider(row))
            score = evaluate_waypoints(result.trace, result.initial_state,
                                      [item.public_targets[name] for name in item.expected_order])
            replays[key] = {"mission_success": result.task_success and score["stopped_order_success"],
                            "stopped": result.final_state["stopped"], "collision": result.final_state["collision"],
                            "error_type": result.error_type, "actions": result.metrics["actions"]}
            save(output / "replay_episodes" / (key + ".json"), {**result.to_dict(), "evaluation": score})
            save(cache_path, replays)
            print(f"Replay {len(replays)}: {row['case_id']} success={replays[key]['mission_success']}", flush=True)
        row["replay_key"] = key
        row["simulation"] = replays[key]
    summary = {"manifest_sha256": manifest_hash, "formal_calls": len(rows),
               "transport_failures": sum(not r["transport_success"] for r in rows),
               "reasoning_responses": sum(r["reasoning_content_present"] for r in rows),
               "unique_replay_episodes": len(replays), "results": {}, "paired": {}}
    for vendor in ("qwen", "deepseek"):
        summary["results"][vendor] = {}
        for arm in ARMS:
            arm_result = {}
            for split in ("existing", "unseen"):
                group = [r for r in rows if r["vendor"] == vendor and r["arm"] == arm and r["split"] == split]
                completed = [r for r in group if r["transport_success"]]
                arm_result[split] = {"runs": len(group), "contract_accepted": sum(r["contract_accepted"] for r in group),
                    "plan_correct": sum(r["plan_correct"] for r in group),
                    "median_response_ms": statistics.median(r["total_ms"] for r in completed) if completed else None,
                    "median_ttft_ms": statistics.median(r["ttft_ms"] for r in completed) if completed else None,
                    "input_tokens": sum(r["usage"].get("prompt_tokens", 0) for r in group),
                    "output_tokens": sum(r["usage"].get("completion_tokens", 0) for r in group),
                    "by_case": {identifier: {"runs": len(items), "accepted": sum(r["contract_accepted"] for r in items),
                                             "correct": sum(r["plan_correct"] for r in items)}
                                for identifier in sorted({r["case_id"] for r in group})
                                for items in [[r for r in group if r["case_id"] == identifier]]}}
                if split == "existing":
                    arm_result[split].update(simulated_success=sum(r.get("simulation", {}).get("mission_success", False) for r in group),
                                            simulation_failures=Counter(r.get("simulation", {}).get("error_type") for r in group
                                                                        if not r.get("simulation", {}).get("mission_success", False)))
            summary["results"][vendor][arm] = arm_result
        lookup = {(r["case_id"], r["repeat"], r["arm"]): r for r in rows if r["vendor"] == vendor}
        summary["paired"][vendor] = {}
        for arm in ARMS[1:]:
            pairs = [(r, lookup[(r["case_id"], r["repeat"], arm)]) for r in rows if r["vendor"] == vendor and r["arm"] == "baseline"]
            summary["paired"][vendor][arm] = {"improved": sum(not a["plan_correct"] and b["plan_correct"] for a, b in pairs),
                                              "regressed": sum(a["plan_correct"] and not b["plan_correct"] for a, b in pairs)}
    save(output / "analyzed_trials.json", rows)
    save(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="runs/planner-prompt-ablation-2026-10-07")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--vendor", choices=("qwen", "deepseek"))
    options = parser.parse_args()
    if sum((options.prepare, options.analyze, bool(options.vendor))) != 1:
        parser.error("Choose exactly one of --prepare, --analyze, --vendor")
    destination = Path(options.output)
    if options.prepare:
        prepare(destination)
    elif options.analyze:
        analyze(destination)
    else:
        run(destination, options.vendor)
