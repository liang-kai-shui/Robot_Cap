# 目标规划提示词对照验证：Qwen3.7-Flash / DeepSeek-Flash

日期：2026-10-07。结论范围：文本目标规划与现有 hybrid 软件导航；不包含视觉或真机。

## 结论

本轮支持优先修正共用提示词。Qwen 的原提示词在已有任务中正确 16/30，改进共用提示词达到 30/30；DeepSeek 两者均 30/30。每组新案例均 16/16。专属适配和互换适配同样通过，尚未发现必须按模型维护不同目标规划提示词的证据。JSON mode 在已完善提示词之上没有额外正确率提升。

## 方法与边界

- 当前生产提示词、TaskPlanner、GoalPlan.parse、任务、评分器和 Runtime 未修改；只新增实验脚本与报告。
- 两个模型各 230 次正式调用，共 460 次。每组：原 10 个任务各 3 次，新 8 个案例各 2 次。
- 所有模型请求前冻结任务、期望值和全部提示词，不根据本轮回答继续调整。新案例是看过上一轮失败后设计的泛化探针，不是外部独立盲测集。
- temperature=0、8192 输出 token 上限、流式、30 秒 socket/read timeout、明确关闭思考。每模型并发 2，组别按固定种子 20261007 交错。没有重试、输出修复或宽松解析。
- B 的角色、schema、200 字符限制、坐标到目录 key 的映射、重复访问和示例同时改善。本轮不能单独确定其中哪个条款贡献最大。
- B–E 的用户上下文、Robot API、目标目录及反馈与 A 相同；C/D 只增加适配文字，E 只增加`response_format={"type":"json_object"}`。未测试 JSON Schema。
- C/D 做互换测试：Qwen 使用 DeepSeek 适配文字，DeepSeek 使用 Qwen 适配文字。能通过这些测试不代表不存在其他任务上的模型差异。
- 原任务 baseline 的完整 messages SHA256 与上一轮一致：Qwen 10/10，DeepSeek 10/10。
- 正式调用接口错误 0，输出 reasoning_content 的响应 0。

## 原有任务：输出契约、任务理解及导航

契约接受表示通过原解析器；计划正确要求访问次序、重复访问和返回目标完全匹配私有评测值。

| 分组 | Qwen 契约接受 | Qwen 计划正确 | Qwen 导航回放映射成功 | DeepSeek 契约接受 / 计划正确 / 回放成功 |
|---|---:|---:|---:|---:|
| A 原共用提示词 | 16/30 | 16/30 | 16/30 | 30/30 · 30/30 · 30/30 |
| B 改进共用提示词 | 30/30 | 30/30 | 30/30 | 30/30 · 30/30 · 30/30 |
| C B＋本模型适配 | 30/30 | 30/30 | 30/30 | 30/30 · 30/30 · 30/30 |
| D B＋另一模型适配 | 30/30 | 30/30 | 30/30 | 30/30 · 30/30 · 30/30 |
| E B＋JSON mode | 30/30 | 30/30 | 30/30 | 30/30 · 30/30 · 30/30 |

离线回放实际执行了 **38 个不同任务＋原始回答组合**，使用原 MissionRunner、真实 worker 与按序停稳评分器；相同任务和完全相同回答复用同一回放结果。表中 300 次原任务回答的映射成绩不是 300 次独立执行，也不是重新调用模型的端到端延迟。回放只含一次真实记录的计划，若导航要求重新规划则安全失败，不编造模型回答。

Qwen A 的 14 次失败：H1–H4 共 12 次返回目标对象或虚构 ID；M3 共 2 次因超过 200 字符的停止原因被拒绝。改进组的计划均能驱动原导航完成任务。所有回放最终停稳、零碰撞。

## 新案例

| 案例 | 检查内容 | 所有五组、两模型 |
|---|---|---:|
| U1 | 由坐标解析任意目录 ID dock_K7 | 各 2/2 |
| U2 | 中文 ID、重复访问 | 各 2/2 |
| U3 | 负坐标、排除未要求目标 | 各 2/2 |
| U4 | 重规划删除已执行前缀，保留后续重复访问 | 各 2/2 |
| U5 | 非原点出发，明确返回出发点 | 各 2/2 |
| U6 | 英文指令、目录顺序不同于任务顺序、重规划 | 各 2/2 |
| U7 | 目标未注册：必须中止 | 各 2/2 |
| U8 | 急停锁定及明确停止指令：必须中止 | 各 2/2 |

新案例只测目标规划，不进行环境导航回放。其中 U7/U8 检查中止而不是强制模型继续运动。Qwen A 在新案例也全部正确，原问题集中于特定既有输入表达。

## 响应耗时与输出 token

以下是每组全部 46 次请求的完整响应中位数，包含错误回答；并发与网络缓存会影响耗时。小幅差异不宜直接解释为模型性能差异。

| 分组 | Qwen 完整响应中位数 | DeepSeek 完整响应中位数 | Qwen 输出 token 总数 | DeepSeek 输出 token 总数 |
|---|---:|---:|---:|---:|
| A 原共用提示词 | 0.746 秒 | 0.789 秒 | 1526 | 506 |
| B 改进共用提示词 | 0.586 秒 | 0.793 秒 | 472 | 529 |
| C B＋本模型适配 | 0.576 秒 | 0.780 秒 | 469 | 506 |
| D B＋另一模型适配 | 0.588 秒 | 0.769 秒 | 483 | 534 |
| E B＋JSON mode | 0.528 秒 | 0.807 秒 | 471 | 521 |

提示词更明确也增加了输入：Qwen A/B 输入 token 为 21,884 / 28,114（约 +28.5%），DeepSeek 为 21,482 / 28,130（约 +30.9%）。专属适配 C 进一步增加到 31,058 / 29,786，未增加本轮正确率。后续正式接入时可以做简短版本的独立验证；本轮不能宣称总 token 或费用下降。

## 建议

1. 下一步将 B 的共用目标规划契约正式接入；保留生产解析器与安全边界。
2. 为 provider 保留模型配置入口，用于思考开关、JSON mode 和以后视觉输入；当前无需默认增加专属提示词文字。
3. JSON mode 两家实测可用，可作为格式保障；它不约束公开 ID、访问顺序或真实安全条件，仍需原解析器和评分器。
4. 正式接入后再跑 full regression，观察复杂策略和 direct 动作生成。本次不能解释或解决这些模式、定位误差及视觉输入的失败。
5. 本次样本有限且达到满分，不能证明新任务普遍可靠；同一任务的重复调用不是独立任务样本，模型别名与后台版本也可能变化。

## 软件回归与复现

`python -m pytest -q --basetemp runs/planner-prompt-ablation-2026-10-07/pytest-temp`：**196 passed in 13.33s**。第一次默认临时目录运行为 189 passed / 7 setup errors，原因是沙箱临时目录写入权限；指定仓库内临时目录后全部通过。没有更改测试或代码以规避失败。

复现入口：

```powershell
python scripts/verify_planner_prompts.py --prepare --output runs/new-planner-ablation
# 分别在临时环境中设置 BENCH_API_KEY，再运行对应 vendor；脚本读取后移除变量。
python scripts/verify_planner_prompts.py --vendor qwen --output runs/new-planner-ablation
python scripts/verify_planner_prompts.py --vendor deepseek --output runs/new-planner-ablation
python scripts/verify_planner_prompts.py --analyze --output runs/new-planner-ablation
```

本轮完整原始响应、usage、时间、冻结 prompts 与模拟 episode 保存在本机 `runs/planner-prompt-ablation-2026-10-07/`；汇总 JSON 和 460 条 CSV 台账随报告保存。凭证不写入项目、实验清单或报告。

## 接口参考

- [阿里结构化输出说明](https://help.aliyun.com/en/model-studio/qwen-structured-output)：JSON Object 与 JSON Schema 的范围不同。
- [DeepSeek JSON mode](https://api-docs.deepseek.com/guides/json_mode/)：JSON 参数、提示词中的 JSON 指令及输出示例。
