# Robot_Cap 交互导航约束与反馈优化实测

日期：2026-10-05；代码基线：`a3cc56c`，分支 `v1-runtime-hardening`。

## 结论

这轮实现了单动作决策、动作后重新观测、近期执行反馈和可信侧前进检查，并完成真实 DeepSeek Flash 测量。**动作约束与诊断能力得到加强，但隐藏地图导航仍未达到可靠可用水平。** 没有充分证据证明 Prompt 优化能稳定提高任务成功率。

最终代码无思考模式成功 **2/12**，低思考模式 **5/12**；旧无思考实现为 **3/12**。低思考有一定成功数提升，同时明显增加等待和输出 token；每题仅 3 次，不能据此认定稳定改善。新实现两组均无碰撞、越界，共有 3 次前进请求被 guard 拒绝；这不是物理安全保证，也不能代替导航成功率。

## 参考已有方案，适配当前项目

| 一手参考 | 借鉴 | 本次实现 |
|---|---|---|
| [Inner Monologue](https://innermonologue.github.io/) | 环境观测和执行成败反馈回到规划过程 | 当前观测、上轮错误、最近 4 轮动作与位姿进入下一轮 Prompt |
| [Code as Policies](https://code-as-policies.github.io/) | 生成代码调用机器人 primitive API | 保留 Python/Registry/Proxy/Runtime；交互实验缩小为一个注册动作调用 |
| [Nav2 Collision Monitor](https://docs.nav2.org/rolling/tutorials/general_tutorials/using_collision_monitor/using_collision_monitor/) | 传感器安全检查独立于规划器 | 注册 handler 的轻量前进 guard；不引入 ROS，也不复现持续速度监控 |

这是现有轻量架构上的适配，不是上述系统的完整复现。没有新增硬件 API、Policy Reuse、检索或自动代码修复。

## 改动与安全边界

1. **交互输出有可执行硬约束。** `validate_decision_policy` 先使用原 Registry 驱动的 Validator，再要求一个直接注册动作调用和字面量参数。不能同轮 `turn` 后 `move`、调用观测 API 或运行循环。任意临时注册的多层 action path 仍可使用；没有引入新 DSL。普通 Benchmark 的契约不变。Runtime 还独立限制每轮一个动作。
2. **每次动作后重新观测。** 默认每任务最多 8 个决策、累计 20 个动作；可通过 `--max-decisions` 调整。每轮保留最近 4 步的前后位姿、动作、前向距离、成败，避免只看当前距离而丢失绕行过程。
3. **前进前重新测距。** 复制调用者 Registry，仅包装已注册的 `motion.move`；原始 handler、Validator、Proxy 与 Dispatcher 核心不变。允许距离为 `max(0, min(1.5m, min(2m, reading)-0.15m))`。禁止没有后向观测保护的倒车；传感器缺失或无效时拒绝前进。测距 handler 和 move handler 使用同一取消信号、同一 action deadline。
4. **安全拒绝可进入下一轮反馈。** `MotionSafetyError` 会紧急停止，下一轮重新观测并带上错误；其他执行/生成/校验错误终止任务。没有改写失败代码或读取历史 Policy。
5. **补齐诊断数据。** 每轮保存错误消息、执行后位姿、`safety_checks`、生成耗时（含失败请求）和可用 TTFT。安全检查直接调用可信测距 handler，不占用 Worker command ID；检查单独落盘，外层 move 的失败仍在 Runtime trace 中。
6. **模型请求配置独立。** `LLM_REQUEST_TIMEOUT_SECONDS`/`--request-timeout` 控制 socket/read 超时，不是整次请求的硬性总期限。它与 Policy compute、action deadline、IPC communication timeout 分开。读取超时为 `PolicyGenerationTimeoutError`；HTTP 错误保留原因并过滤当前密钥。`LLM_REASONING_EFFORT`/`--reasoning-effort` 可选，未设置时不改变服务默认行为。

Guard 不读取隐藏世界地图，依赖 handler 确实返回当前测距、遵守 bounded I/O 和 cancellation contract。它只是**动作前、当前方向、单束测距**保护：不监控动作执行过程，也无法保证覆盖侧面足迹、测距噪声或测距后才出现的障碍。0.15m 是未标定的实验余量。取消仍为 cooperative cancellation，不能硬杀不合作的 Python handler。

## 测量方法与可比性

- 模型 `deepseek-flash`，官方 Chat Completions，非流式，socket/read timeout 30 秒。
- H1–H4 原世界、原起点、原目标不变；每题重复 3 次。地图不提供给模型，位姿是真值、测距是理想值并截断为 2 米。
- 新版本固定 8 个决策、20 个累计动作，每轮最多 1 个动作；旧实现同为 8/20，但每轮最多 3 个动作。信息与决策粒度改变，不能称为仅更改 Prompt 的消融。
- 旧无思考组采用 `thinking.type=disabled`；本次用 `reasoning_effort=none`，官方定义同为关闭思考。低思考仅改为 `low`。[DeepSeek 接口定义](https://api-docs.deepseek.com/api/create-chat-completion/)
- 保留了一个中间 Prompt 尝试：它继承普通 Policy DSL 规则，再追加单动作约束。最终 Prompt 去掉旧 DSL 说明，避免先允许循环再禁止循环。中间结果不算最终代码成绩。
- 每轮新 Worker 启动、观测、校验与评估都计入总耗时。每任务早失败会缩短总耗时；单调用更快不代表整任务更快。Token 只累计 API 已返回 usage 的请求，超时请求在服务端的实际成本未知。
- 没有改动 `task/benchmark_tasks.py`、H1–H4 任务或评分器来提高成绩。所有模型结果来自真实 API；reference/mock 只验证软件链路。

## 模型结果

| 配置 | 成功 | 总耗时中位/P95（秒） | 单轮生成中位（秒） | 调用数 | 已记录输入/输出 token |
|---|---:|---:|---:|---:|---:|
| 旧实现，无思考 | 3/12 | 3.61 / 11.20 | 1.13 | 46 | 29,159 / 6,156 |
| 中间 Prompt，新约束，无思考 | 4/12 | 8.26 / 10.49 | 0.90 | 78 | 82,667 / 514 |
| 最终版本，无思考 | 2/12 | 8.04 / 9.28 | 0.83 | 90 | 71,927 / 591 |
| 最终版本，低思考 | 5/12 | 24.70 / 55.66 | 2.51 | 70 | 54,366 / 43,943 |

| 任务 | 旧无思考 | 中间无思考 | 最终无思考 | 最终低思考 |
|---|---:|---:|---:|---:|
| H1 空旷 | 3/3 | 3/3 | 1/3 | 3/3 |
| H2 近障碍 | 0/3 | 0/3 | 1/3 | 1/3 |
| H3 中距障碍 | 0/3 | 0/3 | 0/3 | 0/3 |
| H4 远障碍 | 0/3 | 1/3 | 0/3 | 1/3 |

最终无思考的 10 次失败均为 `DecisionLimitExceeded`；过程中 2 次 `MotionSafetyError` 被拒绝并重新观测，其中 H2 一次最终完成。模型的主要问题是转向反复和绕行过远，即使单调用输出合规也不能完成导航。

最终低思考的 7 次失败为：4 次 `DecisionLimitExceeded`、1 次 `PolicyGenerationTimeoutError`、2 次 `PolicySyntaxError`；过程中另有 1 次安全拒绝。语法失败来自响应混入解释文字。读取超时现在保存明确原因，而非只有模糊的生成错误类型。

旧无思考组本身也无碰撞，故不能声称新 guard 在同条件统计上降低了碰撞率。新 guard 的有效性由针对性测试和真实运行中被拒绝的请求支持；此前默认思考旧实现出现的碰撞/越界属于另一条件，详见原报告。当前 4 个静态世界、每题 3 次的样本不足以估计真机成功率。

## 回归与针对性测试

| 验证 | 真实结果 |
|---|---|
| `python -m pytest -q --basetemp D:\Robot_Cap\.pytest-tmp-navigation-final-2` | **132 passed in 12.08s** |
| 原 Mock Benchmark | 能力任务 **18/18**，固定安全测试 **2/2** |
| 复杂已知地图 reference | **12/12** 严格判据通过 |
| 单动作交互 reference | **4/4**；H1/H2/H3/H4 分别 2/7/8/7 个决策 |

新增测试覆盖：未执行前拦截多动作/循环/观测调用，可信侧一动作预算，转向后重新观测，近期历史有界，安全拒绝后反馈并继续，失败生成耗时与消息，测距在规划后变化，缺失/NaN/Inf/负数/bool 传感器值，倒车拒绝，复制 Registry 不修改调用者，任意注册 action path，以及测距卡住时 action deadline 取消与 emergency stop。Provider 测试验证超时配置、思考配置、读取超时区分和 HTTP 错误密钥过滤。

## 复现与记录

```powershell
# 在进程环境配置 LLM_BASE_URL、LLM_API_KEY；不要把密钥写入仓库。
python interactive_benchmark.py --provider openai-compatible --model deepseek-flash --reasoning-effort none --request-timeout 30 --runs 3 --output runs/deepseek-interactive-v2-concise-20261005
python interactive_benchmark.py --provider openai-compatible --model deepseek-flash --reasoning-effort low --request-timeout 30 --runs 3 --output runs/deepseek-interactive-v2-low-20261005
python scripts/analyze_navigation_optimization.py
```

本地完整 episode/trace：

- `runs/deepseek-interactive-v2-20261005/`：中间 Prompt 尝试。
- `runs/deepseek-interactive-v2-concise-20261005/`：最终无思考。
- `runs/deepseek-interactive-v2-low-20261005/`：最终低思考。
- `runs/benchmark-regression-navigation/`、`runs/complex-benchmark-regression-navigation/`、`runs/interactive-reference-v2/`：离线回归。

提交到仓库的 `interactive_optimization_2026-10-05_summary.json` 包含统计及各 episode 的动作、前后位姿、错误和安全检查，便于审查失败。旧记录没有逐轮最终位姿，汇总中该项保留 `null`。分析脚本需要上述本地原始目录；运行新代码无法复现已移除的中间 Prompt 或旧版本，需使用对应旧源码。模型服务负载与采样会变化，不保证新运行得到相同数字。

## 下一步建议

优先评测**观测驱动的局部地图与确定性局部规划器辅助**，让 LLM 处理子目标和异常，降低逐个 move/turn 都依赖语言模型推理的频率。应保留当前单动作执行、可信 guard 和反馈记录，用不同目标/随机地图对比；这属于导航层实验，不需要 Policy Reuse。只有前向单束距离时，仍需用转向获得额外观测，不能把未知空间当作可通行区域。

本次没有继续加入规划器或放宽预算来包装成功率。当前结果足以表明：单动作闭环是有用的执行边界，但“LLM 逐动作导航”尚不是稳定的小车方案。

## 文件变更

- Added：`robot/navigation_safety.py`、`runtime/decision_validator.py`、`tests/test_navigation_safety.py`、`scripts/analyze_navigation_optimization.py`、本报告及其 JSON 汇总。
- Modified：`agent/coder.py`、`agent/interactive.py`、`interactive_benchmark.py`、`providers/openai_compatible.py`、`runtime/errors.py`、`tests/test_interactive.py`、`tests/test_openai_compatible.py`、`.env.example`、`README.md`。
- Deleted：无。
