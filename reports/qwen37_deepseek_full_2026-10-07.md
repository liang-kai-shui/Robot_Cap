# Qwen3.7-Flash 与 DeepSeek-Flash：当前项目全量任务实测

日期：2026-10-07。基础代码：`v1-runtime-hardening` / `c3cfb6b635dad9be1d443a4cb751f371b5c0ef55`。
本轮测量全部现有任务系列，模型组每题重复 3 次，统一为非思考模式。没有修改任务、Prompt、评分器或 Runtime 限额，也没有加入 Policy reuse 或硬件功能。

## 1. 结论

当前接口契约下，推荐继续用 **DeepSeek-Flash + hybrid** 作为文本任务规划基线。Qwen3.7-Flash 在复杂已知地图严格评分上更好，但其目标规划输出与当前契约存在明显兼容问题，应先约束公开目标 ID 和输出结构，再复测。
两家直接逐步生成运动代码都暴露出局部循环、动作超限和预算耗尽问题。模型选择需要结合任务层级；本轮结果支持继续保留可信导航器承担局部运动规划。

## 2. 同口径设置与范围

| 设置 | Qwen | DeepSeek |
|---|---|---|
| 请求 / 返回模型名 | qwen3.7-flash | deepseek-flash |
| 官方接口 | dashscope.aliyuncs.com/compatible-mode/v1 | api.deepseek.com |
| 关闭思考的 HTTP 参数 | `enable_thinking: false` | `thinking: {"type":"disabled"}` |
| 共同参数 | temperature=0；max_tokens=8192；stream=true；末尾 usage | 相同 |
| socket/read 超时 | 30 秒 | 30 秒 |
| 重复次数 | 每题 3 次 | 每题 3 次 |

各模型串行请求，两家作业并行运行；离线回归也在同一电脑上运行。使用同一套代码、任务、公开数据和预算。基础及复杂组的每一对初始 Prompt 哈希均核对；交互后的上下文允许随模型动作不同而变化。未设置模型随机种子，temperature=0 仍可能返回不同答案。
接口实际返回的是上述别名，未固定模型快照；模型服务后续升级和缓存状态会影响复现。所有正式调用均不自动重试、不做生成后修复。两次成功的连通性预检单独保存、不纳入分数；最初被本地网络沙箱阻止的预检也单独记录。

关闭思考分别遵循[阿里官方文档](https://help.aliyun.com/en/model-studio/deep-thinking)和[DeepSeek 官方文档](https://api-docs.deepseek.com/guides/thinking_mode/)，没有使用通用 `reasoning_effort=none` 推测是否关闭。本轮审计 API 返回的 reasoning_content 标志。

| 任务系列 | 每模型 episode 数 | 语义 / 预算 |
|---|---:|---|
| 基础能力 + 固定安全 | 60 | 18×3 能力；2×3 固定非法代码/无限循环 |
| 复杂已知地图 | 36 | 12×3；原有限额及 trace 约束 |
| 隐藏地图交互 | 12 | H1–H4×3；最多 8 决策、20 动作，前向测距 2m |
| Mission hybrid | 30 | 10×3；模型选目标，观测地图导航器执行 |
| Mission direct | 30 | 相同任务/目标规划，模型再逐步生成单动作 |

Mission 两组均使用 80 动作、160 观测、4 次目标规划请求，direct 另有 80 次动作生成请求上限。合计两模型 **336 episodes**，其中 324 个模型任务、12 个固定安全验证；实际 API 调用数随交互行为变化。

## 3. 成功率

| 项目 | Qwen3.7-Flash | DeepSeek-Flash |
|---|---:|---:|
| 基础能力 | 54/54（100.0%） | 54/54（100.0%） |
| 复杂地图：最终目标 | 17/36（47.2%） | 12/36（33.3%） |
| 复杂地图：完整 trace 约束 | 9/36（25.0%） | 3/36（8.3%） |
| 隐藏地图交互 | 3/12（25.0%） | 3/12（25.0%） |
| Mission hybrid | 17/30（56.7%） | 30/30（100.0%） |
| Mission direct | 4/30（13.3%） | 14/30（46.7%） |
| 固定安全验证 | 6/6 | 6/6 |

固定安全题不调用模型，不能把它们通过解释为模型能识别全部危险指令。复杂组完整约束还检查路径检查点、观测时机、速度、动作与距离预算；仅终点正确不能算完整通过。部分检查点及动作预算是评分器的额外约束，严格分数代表当前测试定义，不能泛化为所有合法路径的可行率。

## 4. 延迟、调用和 token

单位：秒。TTFT 为首个非空正文片段；响应时间覆盖正文收完，均含网络和服务等待。

| 系列 | 模型 | episode 中位数 / P95 | API 响应中位数 / P95 | 正文 TTFT 中位数 / P95 | API 次数 |
|---|---|---:|---:|---:|---:|
| base | qwen | 0.768 / 1.016 | 0.596 / 0.833 | 0.360 / 0.674 | 54 |
| base | deepseek | 0.844 / 1.144 | 0.668 / 0.970 | 0.554 / 0.876 | 54 |
| complex | qwen | 5.683 / 19.095 | 5.506 / 18.968 | 0.411 / 0.605 | 36 |
| complex | deepseek | 1.196 / 19.393 | 1.021 / 19.383 | 0.527 / 0.887 | 36 |
| interactive | qwen | 5.680 / 7.209 | 0.574 / 0.830 | 0.449 / 0.729 | 78 |
| interactive | deepseek | 6.358 / 7.032 | 0.633 / 0.886 | 0.546 / 0.774 | 78 |
| mission-hybrid | qwen | 1.702 / 4.213 | 0.868 / 1.419 | 0.499 / 0.681 | 30 |
| mission-hybrid | deepseek | 3.144 / 3.956 | 0.708 / 1.020 | 0.618 / 0.957 | 30 |
| mission-direct | qwen | 8.799 / 58.505 | 0.525 / 0.847 | 0.419 / 0.648 | 793 |
| mission-direct | deepseek | 46.322 / 70.520 | 0.655 / 0.955 | 0.559 / 0.826 | 1224 |

**避免早退偏差：只比较两家均成功的同一 task/repeat。**

| 系列 | 配对成功数 | Qwen episode 中位数 / P95 | DeepSeek episode 中位数 / P95 |
|---|---:|---:|---:|
| base | 54 | 0.768 / 1.016 | 0.844 / 1.144 |
| complex | 3 | 0.914 / 0.974 | 1.111 / 1.149 |
| interactive | 3 | 1.821 / 1.906 | 1.582 / 1.835 |
| mission-hybrid | 17 | 3.088 / 4.268 | 3.016 / 4.056 |
| mission-direct | 4 | 8.799 / 18.294 | 5.596 / 12.165 |

episode 总时间是本机软件运行时间。VirtualBackend 的位姿更新不等待真实车轮完成整段行驶；不能把这些秒数当作实车到达目标的时间。原始记录还保留 nominal_motion_seconds。P4 分组并行只影响离线耗时，不把其耗时当作模型速度。

| 模型 | 正式 API 次数 | 已报告输入 token | 缓存命中 token | 已报告输出 token | 输出 length 截断 | 思考正文标志次数 |
|---|---:|---:|---:|---:|---:|---:|
| qwen | 991 | 928522 | 12672 | 38343 | 0 | 0 |
| deepseek | 1422 | 1239548 | 351450 | 62792 | 6 | 0 |

token 来自服务返回，两个 tokenizer 的计数不能视为等长文本。额外审计日志保留生成失败前已返回的 usage；例如被代码提取器拒绝的截断回答，其 token 不会从本报告总量中消失。API 成功只说明 HTTP/SSE 返回完整，不代表生成内容有效或任务成功。

- qwen：按已报告正式 usage 估算 0.214351–0.214351 CNY；缺失 usage 的调用数 0。
- deepseek：按已报告正式 usage 估算 0.171944–0.343888 USD；缺失 usage 的调用数 0。

估算未包含预检、免费额度、活动、账单调整或未返回 usage 的费用。Qwen 使用北京 input≤32K 的输入/缓存/输出价格 0.2/0.04/0.8 元每百万 token；DeepSeek 给出闲时和忙时价格上下界，分别按 $0.15/$0.003/$0.6 与两倍费率计算。[Qwen 价格](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)，[DeepSeek 价格](https://api-docs.deepseek.com/quick_start/pricing/)。不同币种没有换算。

## 5. 按任务查看

### 复杂已知地图

每格为最终目标通过数 / 完整约束通过数，分母均为 3。

| task | Qwen：终点 / 完整 | DeepSeek：终点 / 完整 |
|---|---:|---:|
| AS1 | 3 / 0 | 1 / 0 |
| AS2 | 2 / 0 | 0 / 0 |
| F1 | 3 / 3 | 3 / 3 |
| F2 | 3 / 1 | 3 / 0 |
| MX1 | 0 / 0 | 0 / 0 |
| MX2 | 0 / 0 | 0 / 0 |
| N1 | 1 / 0 | 0 / 0 |
| N2 | 0 / 0 | 1 / 0 |
| N3 | 2 / 2 | 0 / 0 |
| N4 | 3 / 3 | 2 / 0 |
| SP1 | 0 / 0 | 2 / 0 |
| SP2 | 0 / 0 | 0 / 0 |

### 隐藏地图与 Mission

每格为通过数，分母均为 3。H1–H4 在两个实验中预算和控制方式不同，不能直接拿隐藏地图 8 次决策与 Mission 80 动作比较来归因模型差异。

| task | 隐藏地图 Qwen / DS | hybrid Qwen / DS | direct Qwen / DS |
|---|---:|---:|---:|
| H1 | 3 / 3 | 0 / 3 | 0 / 3 |
| H2 | 0 / 0 | 0 / 3 | 0 / 1 |
| H3 | 0 / 0 | 0 / 3 | 0 / 0 |
| H4 | 0 / 0 | 0 / 3 | 0 / 0 |
| M1 | — | 3 / 3 | 0 / 0 |
| M2 | — | 3 / 3 | 0 / 0 |
| M3 | — | 2 / 3 | 3 / 3 |
| M4 | — | 3 / 3 | 0 / 2 |
| M5 | — | 3 / 3 | 0 / 2 |
| M6 | — | 3 / 3 | 1 / 3 |

## 6. 失败分析

- Qwen hybrid 的 12 个 H 系列失败均违反公开目标 ID 契约。实际回答包括 `target_0`、`target_4_1`，或在 targets 中放坐标对象，当前唯一公开 ID 是 `target`。它们被拦截并保持停止，没有进入导航执行。
- Qwen hybrid 的 M3 第一次回答因尚未观测到自由空间而选择中止，stop_reason 超过 200 字符。该理由把局部导航的信息收集责任提前带入目标规划阶段；即使缩短文字，安全中止也不能计作任务完成。
- DeepSeek complex 的 6 次调用命中 8192 输出 token 上限，后续代码提取失败。固定输出预算真实计入评分，没有额外重试或事后补齐；该结果不能等同于不设输出上限时的模型质量。
- 可执行代码也会出错。例如 DeepSeek N2 回答把从 180° 转向南写成 turn(-90)，实际得到 90°，并调用 move(5.0)，触发 2m 单动作上限。几何、角度符号和分段约束需继续由可信层约束。
- 两家隐藏地图均只完成 H1，H2–H4 每次都在决策预算内未达目标。Mission direct 中的复杂绕障和多目标控制出现长动作链、重复观测/动作、预算耗尽；不能仅用更快的 TTFT 解决导航可靠性。

| 系列 | Qwen error_type 计数 | DeepSeek error_type 计数 |
|---|---|---|
| complex | `{"MoveLimitExceeded": 6, "CollisionError": 6, "WorldBoundaryError": 2, "PolicySyntaxError": 1}` | `{"PolicyGenerationError": 6, "MoveLimitExceeded": 10, "CollisionError": 2, "WorldBoundaryError": 2}` |
| interactive | `{"DecisionLimitExceeded": 9}` | `{"DecisionLimitExceeded": 9}` |
| mission-hybrid | `{"PolicyGenerationError": 13}` | `{}` |
| mission-direct | `{"PolicyGenerationError": 13, "NavigationActionBudgetExceeded": 3, "TurnLimitExceeded": 3, "MoveLimitExceeded": 2, "MissionStopped": 5}` | `{"ModelRequestBudgetExceeded": 13, "TurnLimitExceeded": 2, "NavigationActionBudgetExceeded": 1}` |

错误计数只含有 error_type 的 episode。最终目标、检查点、速度等评分失败也可能在没有执行异常时发生，完整计数见 JSON 的 original_summary / by_task。本轮 2413 次正式 API 调用均完成 HTTP/SSE 返回，6 次 length 截断在后续生成处理阶段被拒绝。

## 7. 软件回归与离线控制组

`pytest`：**196 passed in 13.36s**，exit 0。实际回归使用本机 Python 3.13.15；旧虚拟环境缺少 pytest。

| 离线组 | 结果 |
|---|---|
| mock 原始任务 | 18/18 能力；2/2 安全 |
| complex reference | 12/12 完整约束 |
| interactive reference | 4/4 |
| navigation fixed | 11/11 可达；1/1 不可达安全结束 |
| navigation random | 20/20；seed=20261006 |
| mission reference | 30/30；3 repeats |
| P4 fixed | 330 episodes；10任务×11扰动×3 |
| P4 random | 150 episodes；30世界×5扰动 |

离线合计 **578 episodes**，全部 0 付费模型调用；与模型组共 914 episodes，pytest 另计。P4 使用 oracle 目标顺序及已有导航器，不是任一模型的成绩。

P4 固定 seed=20261006；随机 world_seed=161803、fault seed=20261007。固定组已完成的 83 条记录保留后按 profile 并行续跑，最多 3 个进程。按原始全任务索引生成种子，汇总检查唯一 task/profile/repeat 及 fault_seed，不把分组重复计入。

| P4组 | profile | 私有真值成功 / runs | 误报完成 | 安全中止 | 碰撞尝试 | 停稳 |
|---|---|---:|---:|---:|---:|---:|
| robustness-fixed | age-250ms | 30/30 | 0 | 0 | 0 | 30 |
| robustness-fixed | combined | 29/30 | 1 | 0 | 0 | 30 |
| robustness-fixed | delay-20ms | 30/30 | 0 | 0 | 0 | 30 |
| robustness-fixed | dropout-5pct | 29/30 | 0 | 1 | 0 | 30 |
| robustness-fixed | ideal | 30/30 | 0 | 0 | 0 | 30 |
| robustness-fixed | move-8pct | 30/30 | 0 | 0 | 0 | 30 |
| robustness-fixed | odometry-3pct | 21/30 | 9 | 0 | 0 | 30 |
| robustness-fixed | offset-20cm | 0/30 | 27 | 3 | 0 | 30 |
| robustness-fixed | range-3cm | 30/30 | 0 | 0 | 0 | 30 |
| robustness-fixed | stale-2500ms | 0/30 | 0 | 30 | 0 | 30 |
| robustness-fixed | turn-08deg | 29/30 | 0 | 1 | 0 | 30 |
| robustness-random | combined | 27/30 | 3 | 0 | 0 | 30 |
| robustness-random | ideal | 30/30 | 0 | 0 | 0 | 30 |
| robustness-random | move-8pct | 30/30 | 0 | 0 | 0 | 30 |
| robustness-random | odometry-3pct | 3/30 | 24 | 3 | 0 | 30 |
| robustness-random | range-3cm | 30/30 | 0 | 0 | 0 | 30 |

位姿偏差导致的误报完成仍是现有导航的实车风险，需要独立定位/观测证据解决；换模型不会自动消除该误差。P4 的真值仅用于评分，未泄露给模型、Worker 或导航器。

## 8. 下一步建议

1. 保留 DeepSeek 非思考 hybrid 作为当前稳定对照，公开目标目录与可信导航会话继续分工。
2. 在独立改动中完善 Qwen 的目标输出契约：明确 targets 是公开 ID 字符串数组，给单目标示例；确认接口支持后采用结构化输出/枚举约束。未知 ID 继续拒绝，不把模型自行构造的坐标当成授权目标。然后用相同任务复测。
3. 复杂已知地图若仍需模型一次输出完整代码，单独测一次思考模式与输出预算；本轮仅测非思考，不能据此断言开启思考后的效果。优先优化低频目标规划，不把连续绕障交给逐步猜动作。
4. 再增加真实图像输入及可验证的视觉目标落地测试：检查目标识别、方位/距离依据、观测时间和到达证据。当前模型对照不包含图像输入、真实传感器或实际动力学。
5. Policy reuse 继续暂缓；先处理目标接口兼容和定位误报完成。

当前官方资料均标示 Qwen3.7-Flash 和 deepseek-flash 支持视觉；这一产品能力声明不构成本项目的视觉验证。[Qwen 模型信息](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)，[DeepSeek 模型信息](https://api-docs.deepseek.com/quick_start/pricing/)。每题 3 次及当前少量世界只支持本项目条件下的判断，不能外推到任意环境或实车安全保证。

## 9. 数据与复现

- 同目录 `_summary.json`：完整分组统计、配对成功耗时、按题结果、代码哈希、P4 控制组。
- 同目录 `_episodes.csv`：336 条模型/固定安全 episode 的逐条台账；CSV 中基础/复杂生成提取失败可能缺少原有 episode token，完整已报告费用用 API 审计日志汇总。
- 本机 `runs/flash-comparison-2026-10-07/`：API 审计、原始 JSONL、完整有效 Policy、模型目标回答、trace、episode、私有真值、进程日志。runs/ 按原有规则不纳入 Git。截断回答的完整正文未被原有 run_task 保留，审计保留其 usage、length 原因和 Prompt 哈希。
- 正式接口错误和缺失 usage 数量见 JSON 的 api 字段；预检与正式样本分离。API key 不写入任何结果文件。

先在当前进程环境中设置 BENCH_API_KEY；不要把 key 写进仓库或命令记录文件。Qwen / DeepSeek 分别切换环境变量后运行：

```powershell
python -u scripts/compare_flash_benchmarks.py --vendor qwen --output runs/new-comparison/qwen
python -u scripts/compare_flash_benchmarks.py --vendor deepseek --output runs/new-comparison/deepseek
python -u scripts/compare_flash_benchmarks.py --vendor controls --output runs/new-comparison/controls
```

脚本默认执行全部系列，每题 3 次；现有完整 suite 会拒绝覆盖。BENCH_BASE_URL 可覆盖官方地址；BENCH_API_KEY 读取后从环境移除，Policy Worker 不继承该凭证。分析脚本读取已有结果，不调用 API。

本次新增评测适配、汇总及离线分组脚本与报告，README 修正跨服务关闭思考说明。项目的机器人代码、任务和评分器保持原版本；本轮没有合并 main。
