# Robot CaP V1 Runtime Hardening

在普通电脑上测量 Code-as-Policy 机器人 Agent 的实验项目。用户给出自然语言任务，Provider 生成 Python Policy；系统校验语法，在独立子进程运行 Policy，由可信主进程执行机器人请求，再由二维虚拟世界判定任务结果并记录指标。当前仍不连接真实硬件。

## 版本

- **V0.1**：冻结于 Git 标签 `v0.1-baseline`，用于与后续实验公平比较。
- **V0.2 Hardening**：收紧生成 Prompt 与受限 Policy DSL 的契约；保留对函数定义和 `+=`、`-=` 等增强赋值的禁止。测距按 9 位小数归一（分辨率 `1e-9` 米）；非零且小于该分辨率的 `move` 抛出 `MoveBelowResolutionError`，不计 Action。显式 `move(0)` 仍是计数、记录日志的空动作。正常小距离移动（如 `0.01` 米）不受影响。
- **V1 Runtime Foundation**：Policy Worker 只持有 `RobotProxy`；可信主进程持有 `RobotRuntime` 和 `VirtualBackend`，负责能力注册、参数与速度限制、动作分发、状态、trace 和紧急停止。保留旧 Robot API 及原有 20 个 Benchmark 任务。
- **V1 Runtime Hardening**：能力成为运行时可注册的 `CapabilitySpec + handler`。Canonical ID 与公开调用路径分离；Prompt、Validator、Proxy 和 Runtime 使用同一 Registry。动作超时会请求协作式取消并触发紧急停止。
- **Observed Navigation P1/P2**：新增应用层观测地图、A* 与边界探索，使用明确的公开目标，继续经过单动作 Worker 与可信 Runtime 执行。此阶段导航本身不调用 LLM。
- **Mission Planning P3**：同一导航会话持续执行多个目标；模型低频选择公开目标顺序，局部导航持续生成受限 Python 动作。新增统一预算的任务对照评测。
- **Static Robustness P4**：独立、可复现的观测和动作扰动实验；估计位姿用于导航，私有真值用于评分。加入测距误差余量、执行偏差界限和停稳后的有限重采样。

V1 保存未来 Policy reuse 所需的 episode 数据，但不检索或复用历史 Policy；也不实现视觉、音频、机械臂或 ESP32 驱动。冻结版本的真实模型结果保留作对比。

## 分支与历史版本

GitHub 保留两条开发线：`main` 保存 V0.1 基线，`v1-runtime-hardening` 是当前持续开发线。历史阶段使用标签复现，避免每完成一个阶段就永久保留一条分支：

| 标签 | 固定提交 | 内容 |
|---|---|---|
| `v0.1-baseline` | `1dc3263` | V0.1 |
| `v0.2-baseline` | `60f3690` | V0.2 Hardening |
| `v1-runtime-foundation-baseline` | `b86749f` | Runtime Foundation |
| `v1-runtime-hardening-baseline` | `1dfe664` | 导航改动前的 Runtime 与 DeepSeek 实测 |

`v0.2-hardening`、`v1-runtime-foundation` 两条远端分支的提交已完整包含在当前开发线，保存以上标签后删除；没有重写提交历史。本地已有的 Foundation 评测 worktree 保留。需要复现时可执行 `git switch --detach <标签>`；需要继续旧版本工作时可从标签创建临时分支。运行原始数据留在本地 `runs/`，评审后的报告和汇总提交在 `reports/`。

## 架构

```text
Natural Language Task → LLMProvider → Python Policy → Registry-backed Validator
                                               ↓
                              Spawned Worker → manifest-limited RobotProxy
                                               │ JSON Pipe / command_id / accepted / result
                                      ─────────┼───────── trust boundary
                                               ↓
                      Parent: RobotRuntime → registered handler → VirtualBackend
                                  │ registry / safety / state / trace   ↓
                                  │                              VirtualWorld
                                  ↓
                       TaskEvaluator → Result + Metrics + Episode
```

Worker 不接收 World、Backend 或 handler，只接收当前公开路径的纯数据 manifest。未注册能力不会进入 Prompt，Validator 和 Proxy 也不会允许调用。新增任意能力只需向 `CapabilityRegistry` 注册 spec 与可信 handler，并可为该部件注册独立的 `emergency_stop` 回调；Runtime 核心无需预知设备类别。`RobotRuntime` 缓存最近 128 个 command ID 的结果，同一 ID 重试不会重复执行动作。`TaskEvaluator` 使用父进程最终状态独立判断任务完成情况。`PhysicalBackend` 目前只是接口占位。

## 安装

要求 Python 3.11+。

```bash
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -e ".[dev]"
python -m pytest
```

也可执行 `python -m pip install -r requirements.txt` 并从项目根目录运行脚本。Windows PowerShell 可使用 `.venv\Scripts\Activate.ps1` 激活环境。

## 配置与环境变量

`config/default.yaml` 定义世界大小、起始姿态、矩形障碍物与 Runtime 限额。角度采用 `[0, 360)`；0° 指向 +X，90° 指向 +Y。前进距离和世界坐标单位为米。

旧配置的 `timeout_seconds` 继续作为 Policy 计算超时的兼容字段，默认 5 秒；可用 `policy_timeout_seconds` 显式覆盖。`RuntimeLimits` 还提供 `default_action_timeout_seconds`（默认 2 秒）、`communication_timeout_seconds`（默认 2 秒）、`max_linear_speed`（默认 0.5 m/s）和 `max_angular_speed`（默认 90 deg/s）；旧配置无需增加这些字段。单个 `CapabilitySpec` 也可设置自己的动作超时。

默认 Provider 为离线 `mock`，无需 API Key。`openai-compatible` 使用以下环境变量：

```text
LLM_BASE_URL=https://provider.example/v1
LLM_API_KEY=your-key
LLM_MODEL=your-model
LLM_REQUEST_TIMEOUT_SECONDS=30
# 可选，仅在服务支持时设置；值的含义由服务决定
LLM_REASONING_EFFORT=none
```

参考 `.env.example`。环境变量需由 shell 或部署环境设置；项目不自动读取 `.env`。该 Provider 默认使用非流式 `/chat/completions`，此时 `llm_ttft_ms` 为 `null`。使用 `--stream` 会按首个非空 Policy 文本片段记录 TTFT，并请求末尾 usage 块；兼容服务若不支持 `stream_options`，可加 `--no-stream-usage`。未返回的 token usage 保持 `null`，V0 不估算缺失指标。

`LLM_REQUEST_TIMEOUT_SECONDS` 是 API socket/read 超时，并非整次请求的硬性总期限，也不是 Policy 或机器人动作超时。读取超时记录为 `PolicyGenerationTimeoutError`；HTTP 错误保留服务端原因并过滤当前密钥。默认不发送 `reasoning_effort`，保持服务原有行为。不能把兼容接口的 `reasoning_effort=none` 当作所有服务通用的关闭思考参数：当前 Qwen 使用 `enable_thinking=false`，DeepSeek Chat Completions 使用 `thinking={"type":"disabled"}`。本轮跨服务评测由专用适配脚本发送这些参数，普通 Provider 尚未增加对应的服务配置。

## CLI

```bash
python main.py --task-id B1 --auto
python main.py --task-id D1
python main.py "向前走2米" --auto
python main.py --provider openai-compatible --model example-model --auto
python main.py --provider openai-compatible --stream --task-id A1 --auto
```

不带任务文本会交互询问。默认在打印生成的 Policy、显示校验通过后询问是否执行；`--auto` 跳过确认。`--config path.yaml` 可选择配置文件。`--task-id` 使用 Benchmark 的起点、世界及结构化成功条件。

对于任意自定义自然语言任务，可通过 `--success-file goal.json` 提供结构化成功条件。例如：

```json
{"type":"compound_all","conditions":[{"type":"pose_near","x":3,"y":1,"tolerance":0.05},{"type":"stopped"}]}
```

没有成功条件的自定义任务仍会生成并执行 Policy，但 `task_success` 固定为 `false`，CLI 会显示未验证提示。`MockProvider` 只识别固定 Benchmark 与几个简单示例；任意文本应使用真实 Provider。

Robot API 保留旧调用，并支持可选速度：

```python
robot.move(0.5)                         # 米；正数前进，负数后退
robot.move(0.5, 0.2)                    # 速度单位 m/s
robot.move(distance=0.5, speed=0.2)
robot.turn(angle=90, speed=45)          # 角度为度，速度单位 deg/s
robot.stop()
robot.get_distance()
robot.get_pose()
robot.get_state()
```

`move` 和 `turn` 是阻塞的有限动作，正常完成后 `state.stopped == True`；完成后无需追加 `stop()`。`stop()` 用于明确要求立即停止或提前中止的任务。单次距离上限 2 米、转角上限 180 度。速度必须是安全范围内的正数，或省略为 `None`。`get_distance()` 返回当前方向上最近障碍物或世界边界的距离。旧路径 `robot.move(...)` 等只是 canonical capability 的公开别名；Registry 可注册任意多层路径，未注册路径始终被拒绝。函数定义、导入、增强赋值和未知调用仍被禁止。

## Benchmark

```bash
python benchmark.py --runs 5
python benchmark.py --runs 1 --task-id A1 --task-id D1
python benchmark.py --provider openai-compatible --model example-model --runs 5
python benchmark.py --provider openai-compatible --model example-model --stream --capabilities-only --runs 5
```

共 20 个固定任务：基础运动 5、顺序组合 5、条件 4、反馈循环 4、安全 2。E1 使用固定非法 Policy 验证静态拦截；E2 使用固定无限循环验证 Worker 终止，超时设为最多 0.5 秒。汇总把 18 个能力任务的 `capability_task_success_rate` 与 2 个安全任务的 `safety_test_pass_rate` 分开计算；其他成功率、延迟、Token、Action 和失败类型指标只统计能力任务。`--capabilities-only` 只运行 18 个能力任务，适合真实模型 baseline。

结果保存到 `runs/benchmark/raw_results.jsonl`、`summary.json`，每次运行的完整记录保存到 `runs/benchmark/individual/`。汇总提供能力任务成功率、安全测试通过率、能力任务的校验失败率、超时率、碰撞率、LLM 与总延迟的中位数和 P95、平均 Token、Policy 行数、Action 数及失败类型。缺失的 Token 值保持 `null`。

## 独立复杂环境 Benchmark

```bash
python complex_benchmark.py --provider reference
python complex_benchmark.py --provider openai-compatible --model example-model --stream --runs 5
python complex_benchmark.py --provider openai-compatible --task-id N1 --task-id MX1
```

`task/complex_benchmark_tasks.py` 提供 12 个独立任务：错位门与狭窄走廊导航、反馈测距、主动测距、速度约束及混合任务。复杂世界可设置 `clearance`，用扩大的矩形障碍和内缩边界表示机器人所需的保守安全间隙；原世界默认 `clearance=0`，原 20 题与 `task/benchmark_tasks.py` 不变。任务地图、初始位姿会作为已知信息提供给模型；这衡量**已知地图上的 Code-as-Policy 规划**，不代表视觉建图、SLAM 或真实动力学。

新评测区分 `execution_success`、仅检查最终状态的 `task_success`，以及额外检查 trace 的 `complex_success`。后者可检查检查点顺序、测距时机与次数、动作数、累计移动距离和逐段速度。输出写在 `runs/complex-benchmark/`，按任务类别分别汇总。`reference` Provider 使用预先写好的参考策略，只用于验证任务可解和评分器正确；参考策略不会出现在真实模型 Prompt 中，**reference 成功率不是模型成绩**。真实模型评测须选择 `openai-compatible`，按原有环境变量配置。

## 交互式局部观测实验

```bash
python interactive_benchmark.py --provider reference
python interactive_benchmark.py --provider openai-compatible --model example-model --runs 5
python interactive_benchmark.py --provider openai-compatible --model deepseek-flash --reasoning-effort none --request-timeout 30 --runs 3
```

这套独立软件实验让相同目标面对四种隐藏地图。模型每轮只收到当前位姿估计、最多 2 米的前向测距、最近 4 轮动作与结果、上一轮错误原因及剩余预算；完整障碍物表只供可信模拟器与评测器使用。`InteractiveRunner` 在同一任务内保留可信 Runtime、虚拟机器人状态、累积 trace 和单调递增的 command ID，每轮 Policy 仍在新 Worker 中校验并执行。默认每任务最多 8 次决策、累计 20 个动作，可用 `--max-decisions` 单独设置决策预算。

交互模式额外强制**一个注册动作调用，参数为字面量**：允许 `robot.move(0.5)` 或 `robot.turn(-90)`，禁止同轮转向再移动、变量、循环及观测调用。普通 Benchmark 的 Python Policy 契约保持原样。转向执行后必须进入下一轮重新测距。可信 Runtime 也设置每轮一个动作的预算。

交互实验通过复制 Registry 包装已注册的 `motion.move` handler：前进前直接调用当前测距 handler，按 `min(1.5m, min(2m, 新测距)-0.15m)` 限制动作距离。传感器缺失、无效值、后退或超出允许距离都拒绝；检查结果保存在每轮 `safety_checks`。测距与移动共享取消信号和动作 deadline，不分配额外 Worker command ID。`MotionSafetyError` 会紧急停止并反馈给下一轮；其他失败终止任务。耗尽决策预算也会紧急停止。终点始终由可信侧 `TaskEvaluator` 判定。

这是**动作前的前向单束检查**，不读取隐藏地图，不提供行进中持续监测；无法保证检测侧面障碍、观测后的环境变化或传感器误差。0.15 米是实验余量，不是真实刹车距离。每轮保存错误消息、生成耗时（含失败请求）、可用 TTFT、执行后位姿和安全检查。

`reference` 是使用局部观测的确定性规则，仅用于检查多轮机制和场景可解性，不代表 LLM 成绩。此阶段的位姿仍是模拟器真值，测距是有限范围的理想值；尚无相机图像、视觉模型接口、定位误差或真实物理控制。未来接视觉模型时，需要扩展 Provider 的多模态输入并接入实际已注册的观测来源；当前交互式实验不实现 Policy Reuse。

### 参考方案与取舍

| 参考 | 本项目采用的做法 | 当前边界 |
|---|---|---|
| [Inner Monologue](https://innermonologue.github.io/) | 将环境观测和执行反馈放回下一轮决策 | 保存有限近期历史，不输出或保存模型思维链 |
| [Code as Policies](https://code-as-policies.github.io/) | 模型通过明确的机器人 primitive API 生成动作代码 | 交互模式限定单调用，保留已有 Registry/Proxy/Runtime |
| [Nav2 Collision Monitor](https://docs.nav2.org/rolling/tutorials/general_tutorials/using_collision_monitor/using_collision_monitor/) | 借鉴独立于规划器的传感器安全检查职责 | 仅实现轻量动作前 guard，不接 ROS，不等同于持续 Collision Monitor |

这些是针对现有项目的设计借鉴；论文和上游系统的成绩不能作为本项目成绩。模型实测见 `reports/`，后续仍需不同目标、随机地图、噪声与动态障碍测试。

2026-10-05 实测：最终交互版本 DeepSeek Flash 无思考 **2/12**、低思考 **5/12**，总耗时中位数分别 8.04/24.70 秒；隐藏地图导航仍不可靠。动作约束通过不等于任务成功。详见 [交互优化实测报告](reports/interactive_optimization_2026-10-05.md) 与 [此前各方案对照](reports/deepseek_flash_2026-10-05.md)。

2026-10-06 在导航 P1/P2 提交上重跑 DeepSeek Flash 全量测试：基础能力 **54/54**、固定安全测试 **6/6**、复杂已知地图最终目标 **33/36**（严格 trace **23/36**）、低思考隐藏地图交互 **7/12**。另外复测确定性导航固定可达 **11/11**、随机可达 **20/20**；后两组不调用模型，与 8 次决策的交互组不能直接比较。详见 [DeepSeek 全量复测报告](reports/deepseek_full_rerun_2026-10-06.md)。

## 观测地图与确定性导航：P1/P2

```bash
python navigation_benchmark.py
python navigation_benchmark.py --task-id H1 --task-id H2 --task-id H3 --task-id H4
python navigation_benchmark.py --random-worlds 20 --seed 20261006 --output runs/navigation-seeded-evaluation
```

安装后也可使用 `robot-cap-navigation-benchmark`。默认最多 80 个动作、160 次观测，栅格分辨率 0.25 米；可通过 `--max-actions`、`--max-observations`、`--resolution` 配置。配置文件中更严格的 `max_actions` 仍生效。

`navigation/` 位于应用层：公开 `NavigationGoal` 与评测成功条件分别传入，规划器只接收观测。当前测距适配器保留 `distance_m/max_range_m/hit/valid/timestamp/pose/capability_id`；超过 2 米的读数表示量程内无命中，端点保持未知，恰好 2 米的命中可区分。读数无效、过期或测量位姿与当前状态不符时安全结束。

地图只按观测射线更新，分为可通行、占用、未知；当前距离来自已经考虑 `clearance` 的配置空间，不重复膨胀障碍。A* 只通过已观测可通行的四邻接栅格。目标没有已知路线时，选择可到达的观测位置，保持该观测目标直到测量完成，避免途中反复改选位置。通过现有 `turn()` 取得其他方向的观测；扫描转向计入动作数。地图、访问记录和规划状态仅存于本次任务。

规划器持有局部路径，但每轮只生成一个有限动作调用。`NavigationAdapter` 从 Registry 找到当前公开别名，随后经过现有 Validator → Worker → Proxy → Runtime → 前进 guard → Backend。核心 Registry、Proxy 与 Runtime 的分发逻辑未修改，也没有新增 `navigate/follow_path/drive` 机器人 API。每次动作后重新测距和规划。没有已知路线不会直接被判定为目标不可达；信息、动作或进度预算不足时紧急停止并保存原因。

本套评测含原 H1–H4（原任务不变）、不同起点/目标、非栅格目标、错位墙、死胡同、走廊和不可达目标；还可按固定种子生成静态地图。生成器用私有世界检查起终点与连通性，规划器不接收这些信息。`scenarios.json` 是单独保存的私有评测清单；逐次结果保存公开目标、每轮 Policy/观测/trace、安全检查、最终观测地图、API surface 与实际使用能力。

实测固定可达场景 **11/11**，不可达场景 **1/1 安全结束**；固定种子 20 个随机可达地图 **20/20**，均无碰撞或越界。这是零模型调用的确定性导航成绩，采用 80 动作预算，不能直接替代此前 8 次决策的 DeepSeek 成绩。完整结果见 [P1/P2 报告](reports/navigation_foundation_p1_p2.md)。

`total_ms` 包含真实 Worker 启动和执行开销；`nominal_motion_seconds` 仅按配置的最高线速度与角速度估算运动时间，虚拟动作仍即时完成，不能作为实车耗时。本节默认模拟器使用理想观测和定位；可选噪声与漂移实验见 P4。动态障碍、原生视觉输入或 Policy Reuse 尚未实现；低频 LLM 任务规划见下一节 P3。

## 低频任务规划与持续导航：P3

```bash
# 离线检查任务和评测器（全部使用参考实现，不是模型成绩）
python mission_benchmark.py --provider reference
# DeepSeek 配置沿用 LLM_BASE_URL / LLM_API_KEY
python mission_benchmark.py --provider openai-compatible --model deepseek-flash --runs 5 --output runs/p3-deepseek
# 只测低频规划；任务可用 --task-id 筛选
python mission_benchmark.py --provider openai-compatible --model deepseek-flash --strategy hybrid
```

默认三组：`reference` 直接提供评测器的正确目标顺序，作为导航基线；`hybrid` 由模型理解任务、选择目标顺序，再由 P2 导航；`direct` 使用同样的模型任务拆解入口，随后每个动作都由模型生成。这是新的 P3 对照协议，原有 8 决策交互测试保留。

三个方案共用 80 动作、160 观测和现有速度、安全限额。模型任务规划最多 4 次；`direct` 另有最多 80 次动作请求。默认 DeepSeek 使用 `low` 思考、30 秒 API 读超时、非流式输出。模型仅接收公开目标目录、实际观测和执行反馈；私有地图与评测目标顺序不进入模型输入。参考模式的正确顺序属于 oracle 信息，不能作为模型成绩。

目标计划是应用层任务数据 `{"targets":["A","B","start"]}`，只能引用已提供的目标 ID；最多 8 次访问，允许重复访问。模型也可返回 `{"stop_reason":"原因"}` 请求安全中止，这不会被判为任务完成。计划数据不在 Worker 中执行。实际 Policy 继续是现有注册路径的单次 Python 调用，没有新增机器人 API。

每个 episode 共享一个 Runtime、地图、command ID 序列和累计预算。普通转向、移动和绕障由局部导航完成；任务开始以及持续停滞或观测信息不足时才重新请求目标计划。换目标保留地图，预算不重置。模型请求期间保持停稳，异常和预算耗尽紧急停止；完成状态由可信位置和执行结果确认。

新任务包括 H1–H4，以及顺序访问、逆序、返回起点、重复访问、忽略未要求目标等 6 个语言任务。`waypoints-v2` 分别记录实际路径经过和动作结束处停稳，任务成绩要求按序停稳及最终目标正确；原复杂任务的旧评分器不变。模型给错顺序即使到达最终目标，也会被判为任务失败。

逐次记录包含目标计划、每次模型请求的触发原因/usage/耗时、实际 Python、观测、地图、trace、API surface 和 used capabilities。`raw_results.jsonl` 与汇总每次运行后更新；中断后可用相同参数加 `--resume` 继续，配置或源代码签名变化会拒绝混合结果。

2026-10-06 正式 DeepSeek Flash `low` 对照（10 题 × 5 次）：低频规划＋导航 **50/50**，逐动作生成 **30/50**；总耗时中位数 **3.33 / 16.64 秒**，模型调用 **50 / 937 次**。oracle 导航基线 50/50；三组均零碰撞、最终停稳。逐动作方案在开放场地更省动作，但带障碍任务 H2–H4、M5 全部失败。本批结果采用静态世界、理想测距和公开目标坐标，不能推断真机或视觉目标定位的可靠性。另测 100 个新随机静态可达地图，确定性导航 100/100。详见 [P3 实现与完整对照报告](reports/p3_mission_planning.md) 及 [机器可读汇总](reports/p3_mission_planning_summary.json)。

## 静态世界扰动与独立真值评分：P4

```bash
# 本地测试，无模型 API 调用；默认 11 种工况 × 10 个任务
python robustness_benchmark.py --runs 3 --output runs/p4-static
# 可选择工况；使用新种子生成独立随机地图
python robustness_benchmark.py --random-worlds 30 --world-seed 161803 --seed 20261007 --profile ideal --profile range-3cm --profile move-8pct --profile odometry-3pct --profile combined --output runs/p4-unseen
```

安装后也可使用 `robot-cap-robustness-benchmark`。原 benchmark 任务、评分器和默认 Backend 保持兼容；本实验只使用 oracle 目标顺序＋确定性导航，不重新测量 DeepSeek 任务理解能力，也不实现 Policy reuse。

`PerturbedVirtualBackend` 以独立随机流模拟有界测距误差、读数丢失、样本过期、移动偏差、转向偏差、里程计距离比例误差、初始定位偏移和可取消的动作等待。导航读取估计位姿；私有 `truth_snapshot` 和真实运动事件仅供评测器使用，不注册到 Capability Registry，不进入 Worker 或规划上下文。角度反馈仍能准确反映模拟转向，未模拟陀螺仪漂移。

新的 `RangeObservation.uncertainty_m` 给出声明的绝对误差界限。规划和动作前 guard 使用距离下界，前进距离再除以声明的最大执行比例；guard 在动作前重新读传感器，拒绝无效或过期数据。P4 在停稳时最多重试 2 次，每次读取消耗累计观测预算，拒绝的读数保留在日志中且不更新地图。连续读数失败后紧急停止。默认 P1/P2/P3 仍使用零重试与原转向容差；P4 各组统一使用 0.25° 转向容差。

评分分别记录 `reported_completion`（根据估计位姿完成）、`actual_mission_success`（私有真值按序停稳＋最终目标正确）、`false_completion`（误报到达）和 `safe_abort`（未完成但最终停稳且无碰撞尝试）。保存定位误差、传感器丢读、重采样、guard 拒绝、请求距离和真实距离。工况参数、种子、代码签名及预算写入实验清单；逐次落盘，允许配置与源码完全相同的 `--resume`。

这些扰动是软件压力测试，数值尚未由你的 N20 电机、编码器或 ToF 实测校准。测距仍是考虑足迹的配置空间距离；数据过期通过回溯时间戳实现，动作等待是真实可取消等待。世界仍为静态几何，碰撞检测会拒绝整段移动，`collision_attempt` 表示碰撞尝试；没有惯性、刹车过程、打滑动力学或动态障碍。已知误差界限下的 guard 不能补偿未知定位误差，也不能替代真机的持续停止保护。

2026-10-06 完成 **480 次零模型调用评测**：固定任务 330 次，新随机地图 150 次。理想、±3 cm 测距误差、±8% 移动偏差在固定和新地图各 30/30；固定丢读/转向噪声各 29/30，持续过期读数全部安全中止。3% 里程计组真实完成固定 **21/30**、新地图 **3/30**，分别有 9/24 次误报；20 cm 初始偏移组 27 次误报、3 次预算中止。两套均最终停稳且无碰撞尝试。位置估计偏差仍需定位与到达确认来处理。全套单元/集成测试 **196 passed**，旧 mock 能力 18/18、安全 2/2。详细设置与边界见 [P4 报告](reports/navigation_robustness_p4.md) 和 [逐次汇总](reports/navigation_robustness_p4_summary.json)。

## Qwen3.7-Flash 与 DeepSeek-Flash 全量对照

2026-10-07 在当前 `c3cfb6b` 基础上按相同任务、Prompt 和预算实测，每题 3 次，统一非思考、temperature=0、8192 输出 token 上限与流式响应。分别通过服务专用参数关闭思考；原始任务和评分器未修改。

| 测试 | Qwen3.7-Flash | DeepSeek-Flash |
|---|---:|---:|
| 基础能力 | 54/54 | 54/54 |
| 复杂地图完整约束 | 9/36 | 3/36 |
| 隐藏地图交互 | 3/12 | 3/12 |
| 目标规划＋可信导航 hybrid | 17/30 | 30/30 |
| 逐步动作 direct | 4/30 | 14/30 |

Qwen 的 hybrid 失败包括 12 次公开目标 ID/结构错误和 1 次超长停止原因；两家共同成功的 17 个 hybrid 样本，软件总时间中位数为 3.09/3.02 秒。继续使用 DeepSeek hybrid 作为文本规划基线，后续先加强 Qwen 的目标输出契约再复测。原始固定安全检查两家各 6/6，不计作模型成绩。软件回归 **196 passed**；另完成 578 次离线控制评测，包括 480 次 P4 扰动，定位误报完成问题仍存在。

两家共 2413 次正式 API 调用；本轮未输入图片，软件执行时间也不代表实车行驶时间。完整设置、逐题成绩、延迟、token、费用估算和失败分析见 [评测报告](reports/qwen37_deepseek_full_2026-10-07.md)、[汇总 JSON](reports/qwen37_deepseek_full_2026-10-07_summary.json) 和 [逐条台账](reports/qwen37_deepseek_full_2026-10-07_episodes.csv)。复测入口为 `scripts/compare_flash_benchmarks.py`，凭证仅从 `BENCH_API_KEY` 环境变量读取；原始 episode 和 API 审计保存在本机 `runs/flash-comparison-2026-10-07/`。

## 目标规划提示词验证

2026-10-07 又完成 **460 次真实 API 调用**，对比原提示词、改进共用提示词、本模型适配、互换模型适配和 JSON mode。每组包含原 10 个任务各 3 次及新 8 个目标规划案例各 2 次；任务、解析器、导航和评分器保持原样。

原任务的计划正确率：Qwen 原提示词 **16/30**，改进共用提示词 **30/30**；DeepSeek 两者均 **30/30**。所有组的新案例各 **16/16**。专属适配、互换适配和 JSON mode 没有进一步提高正确率，因此下一步优先正式接入共用契约修正，保留模型配置入口。生产提示词本轮尚未替换。

38 个不同任务与回答组合通过真实 worker 离线回放，改进组回答均能满足原按序停稳评分；所有回放最终停稳、零碰撞。回放去重结果不能算作 300 次独立执行。本次只验证文本目标规划，没有重测复杂策略、direct 动作生成、视觉或真机。软件回归 **196 passed**。完整设置、逐组耗时、失败回答及边界见 [验证报告](reports/planner_prompt_ablation_2026-10-07.md)、[汇总](reports/planner_prompt_ablation_2026-10-07_summary.json) 和 [460 条台账](reports/planner_prompt_ablation_2026-10-07_trials.csv)；复测入口 `scripts/verify_planner_prompts.py`。

## Metrics 与运行记录

每次运行都写入 `runs/` 的 JSON，包括任务、Policy、初末状态、兼容旧调用的 Robot API 日志、异常、`RunMetrics` 和 `episode`。Episode 包含 `api_version="v1"`、本次暴露的完整 `api_surface_signature`、从 trace 提取的实际 `used_capabilities`、执行与任务结果及指标；默认 Benchmark 不读取 episode，也不进行 Policy reuse。Trace 记录 `started`、`completed`、`failed` 或 `cancelled`、观测及紧急停止事件，并附稳定的 `capability_id`、command ID、请求参数、结果或错误及可用的状态快照。

`llm_total_ms` 记录请求到完整响应；流式模式下 `llm_ttft_ms` 记录请求到首个非空 Policy 片段；`validation_ms` 为 AST 校验；`execution_ms` 从 Worker 启动到结束或终止；`evaluation_ms` 为任务判定；`total_ms` 从任务开始到结果生成，但排除 CLI 显示 Policy 与用户确认耗时。`presentation_ms` 和 `confirmation_wait_ms` 分别记录这两段时间；无需确认时后者为 `null`。读取姿态、状态和距离不增加 Action 数；`move`、`turn`、`stop` 增加 Action 数。

## 安全模型与限制

V1 的 Worker 与 Backend 有进程边界，但 AST 白名单、受限 builtins 和 `spawn` 子进程仍不能替代操作系统级沙箱；不要将其视为可安全运行任意恶意 Python 的环境。Policy 计算超时累计 worker 执行 Python 的时间，在可信 Runtime 已接受的阻塞调用期间暂停；无限计算仍会被终止并紧急停止。通信超时只限制请求确认及结果传输，不限制已确认动作的正常执行时间。Action deadline 由 Runtime 独立控制；超时会设置 `cancel_event`，随后调用 Backend 的 `emergency_stop()` 并返回 `RobotActionTimeoutError`。这是协作式取消：Python 不能安全杀死运行中的线程。未来物理 handler 必须使用有界 I/O、检查取消信号和 deadline，且紧急停止必须独立于普通动作锁；不遵守契约的 handler 不能安全接入真机。

世界使用轴对齐矩形障碍物；复杂任务的 `clearance` 仅是方形足迹的保守安全间隙，仍不含动力学、精确机器人形状或物理引擎。普通文本任务若无结构化成功条件，无法客观证明任务完成。OpenAI Compatible Provider 的网络端点需自行配置；本项目的离线 Mock 和 reference 测试不验证外部 API 可用性。

## 测试

```bash
python -m pytest
python benchmark.py --provider mock
python complex_benchmark.py --provider reference
python navigation_benchmark.py
python robustness_benchmark.py --profile ideal --task-id H1
```

测试覆盖世界、碰撞、测距、Robot API、任务判定、AST 拒绝、Worker/Backend 隔离、动态能力注册与注销、路径冲突、三种超时的交互、取消与紧急停止、速度限制、command ID 幂等、trace、episode、Mock 端到端链路、Metrics 和 JSON 落盘；P3 另覆盖持续多目标会话、任务计划白名单、重复访问、顺序停稳评分以及累计动作和模型请求预算。P4 增加误差下界、读数重试预算、动作前异常、取消等待、真值隔离和误报到达评分。

## Roadmap

P3 已对照逐动作 LLM、确定性导航和 LLM 目标顺序＋导航；P4 已加入静态观测/执行扰动与独立真值评分。下一步根据扰动结果处理定位不确定性、地图时效和动态障碍，再考虑原生视觉输入与真实 Backend。设备级停止需要实车验证；Policy reuse 继续推迟。
