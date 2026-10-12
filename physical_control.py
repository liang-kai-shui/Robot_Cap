"""Run the trusted Runtime + isolated Policy Worker on the tested real rover.

Default is STOP/status only. --demo runs the tested fixed policy. --instruction
generates a bounded model policy; only --execute opens serial and executes it.
--interactive reuses the connection and model for successive human tasks.
--interactive --mission preflights up to nine motions, then reviews stopped
feedback between segments; it never changes or retries the original plan.
"""
import argparse
import ast
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time

from robot.backends.esp32_serial import ESP32SerialBackend, build_esp32_registry
from robot.runtime import RobotRuntime
from robot.state import Pose
from robot.hardware_limits import MAX_TASK_ACTIONS, MAX_MOTION_CALLS
from runtime.executor import PolicyExecutor
from runtime.limits import RuntimeLimits
from agent.physical_policy import generate_physical_policy, validate_physical_policy, capture_physical_feedback
from agent.physical_context import build_physical_history
from agent.physical_mission import (MISSION_MAX_MOTIONS, MISSION_TIMEOUT_SECONDS,
                                    MissionGuard, split_mission, review_segment)
from providers.openai_compatible import OpenAICompatibleProvider
from policy.episode import EpisodeRecord
from policy.reuse import PhysicalPolicyLibrary, default_library_path
from runtime.errors import PolicyError, RobotEmergencyStopError


DEMO_POLICY = "robot.move(0.30)\nrobot.turn(90)\nrobot.stop()"


def _policy_library(args,log_path):
    if getattr(args,'no_policy_reuse',False):return None
    path=getattr(args,'policy_library',None) or (log_path.parent/'policy-library-hardware.json' if args.log is not None else default_library_path())
    return PhysicalPolicyLibrary(path)


def _finish_episode(task,registry,initial,final,trace,library):
    episode=EpisodeRecord.from_physical(instruction=task.get('instruction') or '',policy=task.get('policy',''),registry=registry,
        initial_state=initial,final_state=final,trace=trace,execution_success=bool(task.get('execution_success')) and not task.get('cancelled'),
        error=task.get('error'),metrics={'scope':'hardware','timings_ms':task.get('timings_ms',{}),'generation':task.get('generation'),
                                     'diagnostics':task.get('diagnostics',{}),'reviews':task.get('reviews',[])})
    task['episode']=episode.to_dict()
    if library:
        if episode.execution_success:library.record(episode,registry)
        elif trace:library.invalidate(episode.instruction,registry)


def _model_provider(args):
    key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    if not key:
        from getpass import getpass
        key = getpass("请输入 DeepSeek API Key（隐藏输入，仅本次进程使用）：").strip()
    if not key:
        raise ValueError("未提供 API Key；未连接串口。")
    return OpenAICompatibleProvider(base_url=args.base_url, api_key=key, model=args.model,
                                    reasoning_effort=getattr(args,'reasoning_effort','none'), stable_api_prefix=True)


def _keyboard_stop(runtime, finished, cancelled):
    import msvcrt
    while not finished.wait(0.02):
        while msvcrt.kbhit():
            key = msvcrt.getwch().lower()
            if key in ("\x00", "\xe0"):
                msvcrt.getwch()
            elif key in ("t", "q", "\x1b", "\x03"):
                cancelled.set()
                runtime.emergency_stop()
                print("已请求停止，后续动作取消。")
                return


def _read_instruction():
    """Accept a fresh console line, discarding keys entered during prior work."""
    import sys
    import msvcrt
    if not sys.stdin.isatty():
        raise ValueError("连续实车终端需要交互式控制台，不接受管道或指令文件。")
    while msvcrt.kbhit():
        msvcrt.getwch()
    return input("任务 > ").strip()


def _require_idle(backend):
    state = backend.snapshot()
    if state["fault"] or not state["connected"] or not state["ready"] or state["stopped"] is not True:
        raise RobotEmergencyStopError("未确认健康停稳，结束会话："+str(state["fault"] or "READY missing"))
    return state


def _physical_limits():
    return RuntimeLimits(max_actions=MAX_TASK_ACTIONS, max_move_distance=0.30, max_turn_angle=90,
                         default_action_timeout_seconds=9, communication_timeout_seconds=2)


@contextmanager
def _timed(timings, name):
    started = time.perf_counter()
    try:
        yield
    finally:
        timings[name] = timings.get(name, 0)+(time.perf_counter()-started)*1000


def _session_generate(provider, instruction, registry, backend, cancelled, execute, *, stage_timings=None, history=None,
                      max_motion_calls=MAX_MOTION_CALLS,policy_library=None,diagnostics=None):
    """A cancelled model request can finish in the background but cannot actuate."""
    completed = threading.Event()
    result = {}
    timings = {}
    feedback = capture_physical_feedback(backend)

    def generate():
        try:
            result["value"] = generate_physical_policy(provider, instruction, registry, feedback=feedback,
                                                       timings=timings, history=history, max_motion_calls=max_motion_calls,policy_library=policy_library,diagnostics=diagnostics)
        except BaseException as exc:
            result["error"] = exc
        finally:
            completed.set()

    thread = threading.Thread(target=generate, name="physical-policy-generation", daemon=True)
    thread.start()
    started = time.perf_counter()
    while True:
        if cancelled.is_set():
            if stage_timings is not None:
                stage_timings["generation_until_cancel"] = (time.perf_counter()-started)*1000
            raise RobotEmergencyStopError("本次模型请求已取消，不执行剩余动作。")
        if execute:
            _require_idle(backend)
        if completed.wait(0.02):
            if stage_timings is not None:
                stage_timings.update(timings)
            if "error" in result:
                raise result["error"]
            return (*result["value"], dict(timings))


def _session_review(provider, instruction, backend, segment, remaining, cancelled, guard, stage_timings, diagnostics=None):
    """Late model replies after a stop/deadline cannot advance the mission."""
    completed, result, timings = threading.Event(), {}, {}

    def review():
        try:
            result["value"] = review_segment(provider, instruction, backend, segment, remaining, timings=timings, diagnostics=diagnostics)
        except BaseException as exc:
            result["error"] = exc
        finally:
            completed.set()

    threading.Thread(target=review, name="physical-feedback-review", daemon=True).start()
    started = time.perf_counter()
    try:
        while True:
            guard.check()
            _require_idle(backend)
            if completed.wait(.02):
                stage_timings.update(timings)
                guard.check()
                if "error" in result:
                    raise result["error"]
                return result["value"]
    finally:
        stage_timings["review_wait"] = (time.perf_counter()-started)*1000


def _run_mission_task(args, provider, backend, runtime, registry, limits, task, cancelled, save,policy_library=None):
    """Advance a preflighted immutable plan only after each stopped segment."""
    task.update(mission=True, mission_limits={"max_motion_calls": MISSION_MAX_MOTIONS,
                "max_seconds": MISSION_TIMEOUT_SECONDS, "segment_max_motion_calls": MAX_MOTION_CALLS},
                segments=[], reviews=[], completed_motion_calls=0)
    attempted = False
    with MissionGuard(runtime, cancelled, args.execute) as guard:
        try:
            print("正在生成完整实车计划，最多9个运动动作；整项任务限时180秒。")
            policy, response, _ = _session_generate(provider, task["instruction"], registry, backend,
                cancelled, args.execute, stage_timings=task["timings_ms"], history=task["model_history"],
                max_motion_calls=MISSION_MAX_MOTIONS,policy_library=policy_library,diagnostics=task.setdefault('diagnostics',{}))
            guard.check()
            policies = split_mission(policy, registry)
            task.update(policy=policy, policy_validated=True, segment_policies=policies,
                        generation={name: getattr(response, name) for name in
                                    ("model", "input_tokens", "output_tokens", "ttft_ms", "total_ms")})
            print("完整计划校验通过：\n"+policy)
            save()
            if not args.execute:
                print(f"仅预览：计划分为{len(policies)}段；反馈决策需要实车执行结果，本次未运行。")
                return
            for index, segment_policy in enumerate(policies):
                guard.check()
                _require_idle(backend)
                # This host count persists across backend.begin_task renewals.
                motions = sum(isinstance(node,ast.Expr) and isinstance(node.value,ast.Call) and
                    isinstance(node.value.func,ast.Attribute) and node.value.func.attr in ('move','reverse','turn','approach')
                    for node in ast.parse(segment_policy).body)
                if task["completed_motion_calls"]+motions > MISSION_MAX_MOTIONS:
                    raise RobotEmergencyStopError("整项任务运动预算耗尽。")
                segment = {"number": index+1, "instruction": task["instruction"], "policy": segment_policy,
                           "execution_success": False, "task_success": None, "error": None, "timings_ms": {}}
                task["segments"].append(segment)
                print(f"执行第{index+1}/{len(policies)}段：\n"+segment_policy)
                with _timed(segment["timings_ms"], "pre_task_standstill"):
                    backend.stop()
                guard.check()
                backend.begin_task()
                guard.check()
                attempted = True
                runtime.begin_decision(MAX_TASK_ACTIONS)
                try:
                    with _timed(segment["timings_ms"], "policy_execution"):
                        outcome = PolicyExecutor(limits).execute(segment_policy, None, Pose(0, 0, 0), runtime=runtime)
                finally:
                    runtime.end_decision()
                segment.update(worker_outcome=asdict(outcome), trace=outcome.trace,
                               execution_success=outcome.success and not cancelled.is_set(),
                               last_feedback_state=backend.snapshot())
                _show_outcome(outcome, cancelled)
                guard.check()
                if not segment["execution_success"]:
                    raise RobotEmergencyStopError(outcome.error_message or "分段执行未完成。")
                _require_idle(backend)
                task["completed_motion_calls"] += motions
                save()
                if index+1 < len(policies):
                    review = {"after_segment": index+1, "timings_ms": {}}
                    task["reviews"].append(review)
                    print("小车已停稳，正在根据本段反馈决定是否继续。")
                    review.update(_session_review(provider, task["instruction"], backend, segment,
                                  policies[index+1:], cancelled, guard, review["timings_ms"], review))
                    _show_timings(review["timings_ms"])
                    save()
                    if review["decision"] != "CONTINUE":
                        raise RobotEmergencyStopError("模型中止剩余计划："+review["reason"])
                    guard.check()
            task["execution_success"] = True
            print("全部计划动作已完成并停稳；实际地面任务结果仍未独立测量。")
        except BaseException:
            # After any attempt, failure is terminal, even with a healthy UART.
            if attempted:
                runtime.emergency_stop()
            raise
        finally:
            task["mission_deadline_expired"] = guard.expired.is_set()
            for key in ("pre_task_standstill", "policy_execution"):
                task["timings_ms"][key] = sum(segment["timings_ms"].get(key, 0) for segment in task["segments"])
            task["timings_ms"]["feedback_review"] = sum(
                review["timings_ms"].get("feedback_review", 0) for review in task["reviews"])


def _show_outcome(outcome, cancelled):
    print("程序执行完成："+str(outcome.success and not cancelled.is_set()))
    for event in outcome.trace:
        if event["phase"] == "completed" and event["kind"] == "action":
            print(event["capability_id"]+": "+json.dumps(event["result"], ensure_ascii=False))
            _show_timings(event["result"].get("timings_ms", {}))
    if outcome.error_type:
        print(outcome.error_type+": "+str(outcome.error_message))


def _show_timings(timings):
    labels = {"model_request": "模型请求", "policy_validation": "代码校验",
              "generation_until_cancel": "生成等待（已中止）", "policy_lookup":"已验证策略检索", "feedback_review": "模型反馈决策",
              "review_wait": "反馈决策等待总耗时",
              "connection_handshake": "串口连接与握手", "pre_task_standstill": "动作前停稳确认",
              "serial_start_wait": "指令至开始确认", "host_start_to_cutoff": "开始至关闭驱动（电脑接收）",
              "firmware_drive": "驱动运行（固件）", "cutoff_to_report": "关闭驱动至测量反馈",
              "report_to_done": "反馈至完成确认", "done_to_stopped_ready": "完成至停稳READY",
              "serial_stop_ack_wait": "停止指令至确认", "ack_to_stopped_ready": "停止确认至停稳READY",
              "action_total": "动作总耗时",
              "policy_execution": "策略执行（含动作与停稳）", "task_total": "任务总耗时"}
    print("环节耗时："+"；".join(f"{labels.get(key,key)} {value/1000:.3f}s" for key,value in timings.items()))


def _interactive(args, log_path):
    """One model/key and one exclusive serial connection, fresh bounded tasks."""
    policy_library=_policy_library(args,log_path)
    backend = ESP32SerialBackend(args.port, max_actions=MAX_TASK_ACTIONS)
    limits = _physical_limits()
    registry = build_esp32_registry(backend)
    journal = {"mode": "interactive", "mission": args.mission, "port": args.port, "preview_only": not args.execute,
               "tasks": [], "task_success": None, "error": None}
    exit_code = 1
    runtime = None
    listener = None
    finished, cancelled = threading.Event(), threading.Event()

    def save():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        provider = _model_provider(args)
        if args.execute:
            print("连续实车控制：测距保护关闭。关闭旧串口程序，小车静止校准，动作方向留出空间。")
            journal["startup_timings_ms"] = {}
            with _timed(journal["startup_timings_ms"], "connection_handshake"):
                backend.connect()
            registry=build_esp32_registry(backend)
            print("已连接 v"+str(backend.snapshot().get("firmware"))+"，front_guard=OFF，收到停稳 READY。")
            _show_timings(journal["startup_timings_ms"])
        else:
            print("连续预览：不打开串口、不执行动作；加 --execute 启用实车执行。")
        print("逐条输入任务并回车。输入 status 查看状态；待命时 T 回车停止、Q 回车退出。")
        print("已启用本次会话上下文：近期任务及已完成动作；重启后清空。“转回去”只反向执行上一条完成任务的转弯。")
        if args.mission:
            print("分段任务模式：完整计划最多9个运动动作，每段最多3个；段间停稳后由模型判断继续或中止。")
        print("生成/执行期间直接按 T/Q 中止并结束会话；Ctrl+C、BOOT 也可停止。")
        save()
        while True:
            if args.execute:
                _require_idle(backend)
            try:
                instruction = _read_instruction()
            except EOFError:
                instruction = "q"
            if args.execute:
                _require_idle(backend)
            if not instruction:
                continue
            command = instruction.lower()
            if command in ("q", "quit", "exit"):
                if args.execute:
                    backend.stop()
                exit_code = 0
                break
            if command in ("status", "状态"):
                print("状态："+json.dumps(backend.snapshot(), ensure_ascii=False))
                continue
            if command in ("t", "stop", "停止"):
                if args.execute:
                    print("停止确认："+json.dumps(backend.stop(), ensure_ascii=False))
                    _require_idle(backend)
                else:
                    print("仅预览，本次未发送停止字节。")
                continue

            runtime = RobotRuntime(backend, limits, registry)
            finished, cancelled = threading.Event(), threading.Event()
            listener = None
            started = time.monotonic()
            task_started = time.perf_counter()
            task = {"number": len(journal["tasks"])+1, "instruction": instruction,
                    "policy": "", "policy_validated": False, "generation": None,
                    "execution_success": False, "task_success": None, "error": None, "timings_ms": {}}
            task["diagnostics"]={}
            task["initial_state"]=backend.snapshot()
            task["model_history"] = build_physical_history(journal["tasks"])
            journal["tasks"].append(task)
            save()
            try:
                if args.execute:
                    listener = threading.Thread(target=_keyboard_stop, args=(runtime, finished, cancelled), daemon=True)
                    listener.start()
                if args.mission:
                    _run_mission_task(args, provider, backend, runtime, registry, limits, task, cancelled, save,policy_library)
                    print("本次处理结束，可以输入下一条任务。")
                    continue
                print("正在生成实车策略，模型："+args.model)
                policy, response, stage_timings = _session_generate(provider, instruction, registry, backend,
                                                     cancelled, args.execute, stage_timings=task["timings_ms"],
                                                     history=task["model_history"],policy_library=policy_library,diagnostics=task["diagnostics"])
                task["timings_ms"].update(stage_timings)
                task.update(policy=policy, policy_validated=True,
                            generation={name: getattr(response, name) for name in
                                        ("model", "input_tokens", "output_tokens", "ttft_ms", "total_ms")})
                if cancelled.is_set():
                    raise RobotEmergencyStopError("本次任务已取消，不执行剩余动作。")
                print("校验通过，本次实车动作：\n"+policy)
                if args.execute:
                    # READY from the previous task may be old after a long
                    # input/model wait. Require a fresh STOP/READY before motion.
                    with _timed(task["timings_ms"], "pre_task_standstill"):
                        backend.stop()
                    backend.begin_task()
                    with _timed(task["timings_ms"], "policy_execution"):
                        outcome = PolicyExecutor(limits).execute(policy, None, Pose(0, 0, 0), runtime=runtime)
                    task["worker_outcome"] = asdict(outcome)
                    task["execution_success"] = outcome.success and not cancelled.is_set()
                    _show_outcome(outcome, cancelled)
                    if not task["execution_success"]:
                        raise RobotEmergencyStopError(outcome.error_message or "策略未完成，结束会话。")
                    _require_idle(backend)
                else:
                    print("仅预览，本次没有串口动作。")
            except PolicyError as exc:
                # Generation/preflight failure cannot send movement. Continue
                # only if the existing hardware connection is still healthy.
                task["error"] = f"{type(exc).__name__}: {exc}"
                print(task["error"])
                if args.execute:
                    _require_idle(backend)
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    cancelled.set()
                    runtime.emergency_stop()
                task["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                finished.set()
                if listener is not None:
                    listener.join(0.5)
                if cancelled.is_set():
                    task["execution_success"] = False
                task.update(cancelled=cancelled.is_set(), last_feedback_state=backend.snapshot(),
                            trace=runtime.trace,
                            serial_log=[item for item in backend.logs if item["time_monotonic"] >= started])
                task["timings_ms"]["task_total"] = (time.perf_counter()-task_started)*1000
                _finish_episode(task,registry,task["initial_state"],backend.snapshot(),runtime.trace,policy_library)
                _show_timings(task["timings_ms"])
                save()
            print("本次处理结束，可以输入下一条任务。")
    except KeyboardInterrupt:
        cancelled.set()
        if backend.snapshot()["connected"]:
            (runtime or RobotRuntime(backend, limits, registry)).emergency_stop()
        journal["error"] = "KeyboardInterrupt; session cancelled"
        print("已中止会话。")
    except Exception as exc:
        journal["error"] = f"{type(exc).__name__}: {exc}"
        print(journal["error"])
        print("会话结束；不会自动重连或续跑。")
    finally:
        finished.set()
        if listener is not None:
            listener.join(0.5)
        journal["last_feedback_state"] = backend.snapshot()
        backend.close()
        journal["serial_tail"] = backend.logs
        journal["exit_code"] = exit_code
        try:
            save()
            print("会话日志："+str(log_path))
        except OSError as exc:
            print(f"日志保存失败：{exc}")
            exit_code = 1
    return exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description="ESP32 rover: status, fixed demo, or preview/execute a bounded natural-language policy")
    parser.add_argument("--port", default="COM5")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--demo", action="store_true", help="Execute the tested three-call policy on the real car")
    mode.add_argument("--instruction", help="Natural-language task; preview only unless --execute is supplied")
    mode.add_argument("--interactive", action="store_true", help="Read successive tasks; preview only unless --execute is supplied")
    parser.add_argument("--execute", action="store_true", help="Execute the generated and validated instruction on the real car")
    parser.add_argument("--mission", action="store_true", help="Interactive segmented plan: up to 9 motions, 180s, stopped feedback reviews")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--reasoning-effort", choices=['none','low','high'], default='none', help="Default non-thinking for lower planning latency")
    parser.add_argument("--base-url", default=os.getenv("LLM_BASE_URL") or "https://api.deepseek.com")
    parser.add_argument('--no-policy-reuse',action='store_true',help='Always ask the model; do not learn/reuse verified recipes')
    parser.add_argument('--policy-library',type=Path,help='Verified recipe file; default shared project hardware library')
    parser.add_argument("--log", type=Path, help="JSON record (default runs/physical/date-time.json)")
    args = parser.parse_args(argv)
    if args.mission and not args.interactive:
        parser.error("--mission 当前须与 --interactive 一起使用；可先省略 --execute 预览完整计划。")
    if args.execute and args.instruction is None and not args.interactive:
        parser.error("--execute 必须与 --instruction 或 --interactive 一起使用；固定演示使用 --demo。")
    log_path = args.log or Path(__file__).resolve().parent/"runs"/"physical"/(datetime.now().strftime("%Y%m%d-%H%M%S-%f")+".json")
    if args.interactive:
        return _interactive(args, log_path)
    policy_library=_policy_library(args,log_path)
    backend = ESP32SerialBackend(args.port, max_actions=MAX_TASK_ACTIONS)
    limits = _physical_limits()
    registry = build_esp32_registry(backend)
    runtime = RobotRuntime(backend, limits, registry)
    policy = DEMO_POLICY if args.demo else ""
    preview_only = args.instruction is not None and not args.execute
    execute_policy = args.demo or args.execute
    finished, cancelled = threading.Event(), threading.Event()
    listener = None
    outcome = None
    generation = None
    diagnostics={}
    initial_state=backend.snapshot()
    timings = {}
    task_started = time.perf_counter()
    policy_validated = False
    error = None
    exit_code = 1
    try:
        if args.instruction is not None:
            print("正在生成实车策略，模型："+args.model+"；尚未连接串口。")
            provider = _model_provider(args)
            # Human key-entry time is outside this task's software latency.
            task_started = time.perf_counter()
            policy, response = generate_physical_policy(provider, args.instruction, registry, timings=timings,policy_library=policy_library,diagnostics=diagnostics)
            generation = {name: getattr(response, name) for name in
                          ("model", "input_tokens", "output_tokens", "ttft_ms", "total_ms")}
        if policy:
            with _timed(timings, "policy_validation"):
                validate_physical_policy(policy, registry)
            policy_validated = True
            print("校验通过，本次实车动作：\n"+policy)
        if preview_only:
            print("仅预览：未打开 COM 口，未发送任何动作。加 --execute 才执行。")
            exit_code = 0
        else:
            print("测距可用性以回传为准；普通前方停车保护关闭。关闭旧键盘程序/串口 monitor；小车平放，先静止校准。")
            if execute_policy:
                print("动作方向留出空间，线材留足松量。终端保持焦点：T/Q 中止，BOOT 也可停止。")
            with _timed(timings, "connection_handshake"):
                backend.connect()
            print("已连接 v"+str(backend.snapshot().get("firmware"))+"，front_guard=OFF，收到停稳 READY。")
            if execute_policy:
                # A disconnected preview schema must never authorize a later unsupported call.
                registry=build_esp32_registry(backend)
                validate_physical_policy(policy,registry)
                runtime.registry=registry
                initial_state=backend.snapshot()
                listener = threading.Thread(target=_keyboard_stop, args=(runtime, finished, cancelled), daemon=True)
                listener.start()
                # Simulator compatibility arguments are unused with this runtime.
                with _timed(timings, "policy_execution"):
                    outcome = PolicyExecutor(limits).execute(policy, None, Pose(0, 0, 0), runtime=runtime)
                exit_code = 0 if outcome.success and not cancelled.is_set() else 1
                _show_outcome(outcome, cancelled)
            else:
                exit_code = 0
                print("只检查连接，本次没有发送运动指令。")
            print("状态："+json.dumps(backend.snapshot(), ensure_ascii=False))
    except KeyboardInterrupt:
        cancelled.set()
        if backend.snapshot()["connected"]:
            runtime.emergency_stop()
        error = "KeyboardInterrupt; remaining actions cancelled"
        print("已中止，后续动作取消。")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(error)
    finally:
        finished.set()
        if listener is not None:
            listener.join(0.5)
        state = backend.snapshot()
        backend.close()
        timings["task_total"] = (time.perf_counter()-task_started)*1000
        _show_timings(timings)
        record = {"backend": "esp32-serial", "port": args.port, "policy": policy, "timings_ms": timings,
                  "instruction": args.instruction, "generation": generation,
                  "policy_validated": policy_validated, "preview_only": preview_only,
                  "execution_success": bool(outcome and outcome.success and not cancelled.is_set()),
                  "status_only": args.instruction is None and not args.demo,
                  "cancelled": cancelled.is_set(), "error": error,
                  "task_success": None, "task_success_note": "No ground truth/physical task evaluator; encoder and gyro estimates only",
                  "last_feedback_state": state, "trace": runtime.trace, "serial_log": backend.logs}
        record["diagnostics"]=diagnostics
        _finish_episode(record,registry,initial_state,state,runtime.trace,policy_library)
        if outcome is not None:
            record["worker_outcome"] = asdict(outcome) if is_dataclass(outcome) else outcome
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            print("日志："+str(log_path))
        except OSError as exc:
            print(f"日志保存失败：{exc}")
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
