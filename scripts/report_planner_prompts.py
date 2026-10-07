"""Export an audited planner ablation report from completed local measurements."""
import csv
import hashlib
import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.task_planner import GoalPlan
from runtime.errors import PolicyGenerationError
from scripts.verify_planner_prompts import ARMS


def main():
    output = ROOT / "runs/planner-prompt-ablation-2026-10-07"
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    frozen = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    rows = json.loads((output / "analyzed_trials.json").read_text(encoding="utf-8"))
    assert summary["formal_calls"] == len(rows) == 460
    assert summary["manifest_sha256"] == hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest()
    cases = {case["id"]: case for case in frozen["cases"]}
    baseline_matches = {}
    for vendor in ("qwen", "deepseek"):
        old = ROOT / f"runs/flash-comparison-2026-10-07/{vendor}/mission-hybrid/api_calls.jsonl"
        old_hashes = {json.loads(line)["prompt_sha256"] for line in old.read_text(encoding="utf-8").splitlines()}
        baseline_matches[vendor] = {case["id"]: hashlib.sha256(json.dumps(case["messages"]).encode()).hexdigest() in old_hashes
                                    for case in cases.values() if case["split"] == "existing"}
    for row in rows:
        case = cases[row["case_id"]]
        messages = case[row["vendor"] + "_messages"][row["arm"]]
        assert row["prompt_sha256"] == hashlib.sha256(json.dumps(messages).encode()).hexdigest()
        if row["transport_success"]:
            try:
                plan = GoalPlan.parse(row["response"], case["catalogue"])
                correct = (plan.stop_reason is not None if case["expected"].get("abort") else
                           plan.stop_reason is None and list(plan.targets) == case["expected"]["targets"])
                assert row["contract_accepted"] and row["plan_correct"] == correct
            except PolicyGenerationError:
                assert not row["contract_accepted"] and not row["plan_correct"]
    summary["baseline_prompt_matches_previous_run"] = baseline_matches
    summary["pytest"] = (output / "pytest.txt").read_text(encoding="utf-8").strip()
    summary["frozen_design"] = {key: value for key, value in frozen.items() if key != "cases"}
    summary["cases"] = [{key: case[key] for key in ("id", "split", "repeats", "catalogue", "expected")}
                        | {"user_message": case["messages"][1]["content"]} for case in cases.values()]
    report_root = ROOT / "reports"
    prefix = report_root / "planner_prompt_ablation_2026-10-07"
    prefix.with_name(prefix.name + "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["vendor", "requested_model", "returned_model", "case_id", "split", "arm", "repeat",
              "transport_success", "contract_accepted", "plan_correct", "ttft_ms", "total_ms",
              "input_tokens", "output_tokens", "simulated_mission_success", "replay_key",
              "parse_error", "response", "prompt_sha256", "request_id"]
    with prefix.with_name(prefix.name + "_trials.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["vendor"], r["arm"], r["case_id"], r["repeat"])):
            record = {key: row.get(key) for key in fields}
            record.update(input_tokens=row["usage"].get("prompt_tokens"),
                          output_tokens=row["usage"].get("completion_tokens"),
                          simulated_mission_success=row.get("simulation", {}).get("mission_success"))
            writer.writerow(record)
    labels = {"baseline": "A 原共用提示词", "shared": "B 改进共用提示词",
              "native_profile": "C B＋本模型适配", "cross_profile": "D B＋另一模型适配",
              "shared_json": "E B＋JSON mode"}
    lines = ["# 目标规划提示词对照验证：Qwen3.7-Flash / DeepSeek-Flash", "",
             "日期：2026-10-07。结论范围：文本目标规划与现有 hybrid 软件导航；不包含视觉或真机。", "",
             "## 结论", "",
             "本轮支持优先修正共用提示词。Qwen 的原提示词在已有任务中正确 16/30，改进共用提示词达到 30/30；"
             "DeepSeek 两者均 30/30。每组新案例均 16/16。专属适配和互换适配同样通过，尚未发现必须按模型"
             "维护不同目标规划提示词的证据。JSON mode 在已完善提示词之上没有额外正确率提升。", "",
             "## 方法与边界", "",
             "- 当前生产提示词、TaskPlanner、GoalPlan.parse、任务、评分器和 Runtime 未修改；只新增实验脚本与报告。",
             "- 两个模型各 230 次正式调用，共 460 次。每组：原 10 个任务各 3 次，新 8 个案例各 2 次。",
             "- 所有模型请求前冻结任务、期望值和全部提示词，不根据本轮回答继续调整。新案例是看过上一轮失败后"
             "设计的泛化探针，不是外部独立盲测集。",
             "- temperature=0、8192 输出 token 上限、流式、30 秒 socket/read timeout、明确关闭思考。"
             "每模型并发 2，组别按固定种子 20261007 交错。没有重试、输出修复或宽松解析。",
             "- B 的角色、schema、200 字符限制、坐标到目录 key 的映射、重复访问和示例同时改善。"
             "本轮不能单独确定其中哪个条款贡献最大。",
             "- B–E 的用户上下文、Robot API、目标目录及反馈与 A 相同；C/D 只增加适配文字，E 只增加"
             "`response_format={\"type\":\"json_object\"}`。未测试 JSON Schema。",
             "- C/D 做互换测试：Qwen 使用 DeepSeek 适配文字，DeepSeek 使用 Qwen 适配文字。"
             "能通过这些测试不代表不存在其他任务上的模型差异。",
             f"- 原任务 baseline 的完整 messages SHA256 与上一轮一致：Qwen {sum(baseline_matches['qwen'].values())}/10，"
             f"DeepSeek {sum(baseline_matches['deepseek'].values())}/10。",
             f"- 正式调用接口错误 {summary['transport_failures']}，输出 reasoning_content 的响应 {summary['reasoning_responses']}。",
             "", "## 原有任务：输出契约、任务理解及导航", "",
             "契约接受表示通过原解析器；计划正确要求访问次序、重复访问和返回目标完全匹配私有评测值。", "",
             "| 分组 | Qwen 契约接受 | Qwen 计划正确 | Qwen 导航回放映射成功 | DeepSeek 契约接受 / 计划正确 / 回放成功 |",
             "|---|---:|---:|---:|---:|"]
    for arm in ARMS:
        q = summary["results"]["qwen"][arm]["existing"]
        d = summary["results"]["deepseek"][arm]["existing"]
        lines.append(f"| {labels[arm]} | {q['contract_accepted']}/30 | {q['plan_correct']}/30 | {q['simulated_success']}/30 | "
                     f"{d['contract_accepted']}/30 · {d['plan_correct']}/30 · {d['simulated_success']}/30 |")
    lines += ["", f"离线回放实际执行了 **{summary['unique_replay_episodes']} 个不同任务＋原始回答组合**，"
              "使用原 MissionRunner、真实 worker 与按序停稳评分器；相同任务和完全相同回答复用同一回放结果。"
              "表中 300 次原任务回答的映射成绩不是 300 次独立执行，也不是重新调用模型的端到端延迟。"
              "回放只含一次真实记录的计划，若导航要求重新规划则安全失败，不编造模型回答。", "",
              "Qwen A 的 14 次失败：H1–H4 共 12 次返回目标对象或虚构 ID；M3 共 2 次因超过 200 字符的停止原因被拒绝。"
              "改进组的计划均能驱动原导航完成任务。所有回放最终停稳、零碰撞。", "",
              "## 新案例", "",
              "| 案例 | 检查内容 | 所有五组、两模型 |", "|---|---|---:|",
              "| U1 | 由坐标解析任意目录 ID dock_K7 | 各 2/2 |",
              "| U2 | 中文 ID、重复访问 | 各 2/2 |",
              "| U3 | 负坐标、排除未要求目标 | 各 2/2 |",
              "| U4 | 重规划删除已执行前缀，保留后续重复访问 | 各 2/2 |",
              "| U5 | 非原点出发，明确返回出发点 | 各 2/2 |",
              "| U6 | 英文指令、目录顺序不同于任务顺序、重规划 | 各 2/2 |",
              "| U7 | 目标未注册：必须中止 | 各 2/2 |",
              "| U8 | 急停锁定及明确停止指令：必须中止 | 各 2/2 |", "",
              "新案例只测目标规划，不进行环境导航回放。其中 U7/U8 检查中止而不是强制模型继续运动。"
              "Qwen A 在新案例也全部正确，原问题集中于特定既有输入表达。", "",
              "## 响应耗时与输出 token", "",
              "以下是每组全部 46 次请求的完整响应中位数，包含错误回答；并发与网络缓存会影响耗时。"
              "小幅差异不宜直接解释为模型性能差异。", "",
              "| 分组 | Qwen 完整响应中位数 | DeepSeek 完整响应中位数 | Qwen 输出 token 总数 | DeepSeek 输出 token 总数 |",
              "|---|---:|---:|---:|---:|"]
    for arm in ARMS:
        groups = {v: [r for r in rows if r["vendor"] == v and r["arm"] == arm] for v in ("qwen", "deepseek")}
        latency = {v: statistics.median(r["total_ms"] for r in g if r["transport_success"]) for v, g in groups.items()}
        tokens = {v: sum(r["usage"].get("completion_tokens", 0) for r in g) for v, g in groups.items()}
        lines.append(f"| {labels[arm]} | {latency['qwen']/1000:.3f} 秒 | {latency['deepseek']/1000:.3f} 秒 | {tokens['qwen']} | {tokens['deepseek']} |")
    lines += ["", "提示词更明确也增加了输入：Qwen A/B 输入 token 为 21,884 / 28,114（约 +28.5%），"
              "DeepSeek 为 21,482 / 28,130（约 +30.9%）。专属适配 C 进一步增加到 31,058 / 29,786，"
              "未增加本轮正确率。后续正式接入时可以做简短版本的独立验证；本轮不能宣称总 token 或费用下降。"]
    lines += ["", "## 建议", "",
              "1. 下一步将 B 的共用目标规划契约正式接入；保留生产解析器与安全边界。",
              "2. 为 provider 保留模型配置入口，用于思考开关、JSON mode 和以后视觉输入；当前无需默认增加专属提示词文字。",
              "3. JSON mode 两家实测可用，可作为格式保障；它不约束公开 ID、访问顺序或真实安全条件，仍需原解析器和评分器。",
              "4. 正式接入后再跑 full regression，观察复杂策略和 direct 动作生成。本次不能解释或解决这些模式、定位误差及视觉输入的失败。",
              "5. 本次样本有限且达到满分，不能证明新任务普遍可靠；同一任务的重复调用不是独立任务样本，模型别名与后台版本也可能变化。", "",
              "## 软件回归与复现", "",
              "`python -m pytest -q --basetemp runs/planner-prompt-ablation-2026-10-07/pytest-temp`：**196 passed in 13.33s**。"
              "第一次默认临时目录运行为 189 passed / 7 setup errors，原因是沙箱临时目录写入权限；"
              "指定仓库内临时目录后全部通过。没有更改测试或代码以规避失败。", "",
              "复现入口：", "", "```powershell",
              "python scripts/verify_planner_prompts.py --prepare --output runs/new-planner-ablation",
              "# 分别在临时环境中设置 BENCH_API_KEY，再运行对应 vendor；脚本读取后移除变量。",
              "python scripts/verify_planner_prompts.py --vendor qwen --output runs/new-planner-ablation",
              "python scripts/verify_planner_prompts.py --vendor deepseek --output runs/new-planner-ablation",
              "python scripts/verify_planner_prompts.py --analyze --output runs/new-planner-ablation", "```", "",
              "本轮完整原始响应、usage、时间、冻结 prompts 与模拟 episode 保存在本机 "
              "`runs/planner-prompt-ablation-2026-10-07/`；汇总 JSON 和 460 条 CSV 台账随报告保存。"
              "凭证不写入项目、实验清单或报告。", "",
              "## 接口参考", "",
              "- [阿里结构化输出说明](https://help.aliyun.com/en/model-studio/qwen-structured-output)：JSON Object 与 JSON Schema 的范围不同。",
              "- [DeepSeek JSON mode](https://api-docs.deepseek.com/guides/json_mode/)：JSON 参数、提示词中的 JSON 指令及输出示例。", ""]
    prefix.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Exported {prefix.name}: {len(rows)} audited trials")


if __name__ == "__main__":
    main()
