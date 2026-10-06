# P3：低频任务规划与持续导航

实现与评测日期：2026-10-06（北京时间）。本阶段接通语言任务拆解、持续观测导航和现有可信动作执行链，保留原始 benchmark 和交互实验。

## 架构

```text
自然语言任务 + 公开目标目录
             ↓
        TaskPlanner
             ↓
     已校验的目标 ID 顺序
             ↓
     NavigationSession
    持续地图 / 全局预算 / 目标进度
             ↓
   LocalNavigator 或逐动作模型
             ↓
    单调用 Python → Validator → Worker → Proxy → Runtime → guard → Backend
```

任务计划是应用层元数据，模型只能选择公开目录里的目标 ID。`GoalPlan` 使用 dataclass 和标准 JSON 校验，最多 8 次访问；保留重复访问，拒绝未知 ID、重复 JSON key、额外字段及代码。模型可以用明确的 `stop_reason` 请求中止；中止不代表任务完成。实际 Worker Policy 仍是受限 Python，没有新增机器人能力或修改通用 Registry、Proxy、Runtime 分发。

`NavigationSession` 在整个 episode 内保留同一个 Runtime、地图和累计预算。`LocalNavigator.set_goal()` 换目标时保留地图、访问统计和执行历史；目标相关的边界选择会重新计算。模型请求前停稳，普通绕障继续由确定性层完成；持续停滞或观测信息耗尽时，可请求新的剩余目标计划，最多 4 次规划请求。动作/观测预算不因重规划重置，观测信息耗尽也不声称目标全局不可达。

每个动作前采样；模型动作等待结束后，可信 guard 还会读取当前方向的新测距。API 读超时继续控制模型网络请求；Policy 计算、动作取消和通信超时沿用现有 Runtime 机制。Python 线程取消仍是协作式契约。

## 参考来源与取舍

采用 [Code as Policies](https://code-as-policies.github.io/) 的语言与机器人代码组合思路、[Inner Monologue](https://innermonologue.github.io/) 的执行反馈，以及 [Nav2 导航服务器](https://docs.nav2.org/rolling/getting_started/navigation_concepts/navigation_servers/) 对任务规划与局部执行的职责划分。实现使用现有项目与标准库，没有引入上游框架。应用层当前处理导航目标；这不改变 Runtime 对未来能力类型的通用性。

## 新配对评测

`mission_benchmark.py` 包含 H1–H4 和六个语言任务：顺序访问后返回、逆序访问、重复访问、忽略未要求目标、带障碍多目标、不同起点返回。公开目标目录提供名称和坐标；模型不接收真实地图、参考策略或私有正确顺序。

| 方案 | 任务拆解 | 动作产生 |
|---|---|---|
| reference | 直接提供评测器的正确顺序（oracle） | P2 确定性导航 |
| hybrid | 模型选择公开目标顺序 | P2 确定性导航 |
| direct | 同样的模型任务拆解入口 | 模型每轮生成一个 Python 动作 |

两个模型方案使用相同任务拆解 Prompt，以减少任务理解差异对局部控制比较的干扰。每个 episode 的动作预算均为 80、观测预算均为 160，安全余量 0.15 米、最大单步 1.5 米。规划请求最多 4 次；direct 另有最多 80 次动作请求。正式模型使用 `deepseek-flash`、`low` 思考、30 秒 API socket/read 超时、非流式响应，每题重复 5 次。

这是新的 P3 协议：direct 也有任务拆解步骤和更大的预算，成绩不能直接与旧版 8 决策实验比较。reference 获得正确目标顺序，其结果是导航上限检查，不是模型成绩。

direct 的动作上下文保留最近 4 步；hybrid 的局部导航持有整个 episode 的观测地图。这是两种架构的组成差异；二者观测来源、安全链与公开目标相同，但本实验不是只改变一个 Prompt 的消融测试。API 服务与机器负载会影响响应时间；三组按任务和重复轮次交错执行，且不在正式评测中调整算法或提示词。

## 评分与记录

新增 `waypoints-v2`，独立检查实际运动线段是否按序经过目标，以及是否在目标位置停稳。P3 指令要求每个目标停稳，因此任务成绩要求 stopped order 和最终状态同时满足。模型漏掉中间访问，即使返回正确最终位置也不会通过。原复杂任务评分器和 `task/benchmark_tasks.py` 未修改。

结果保留 episode ID、指令、API 版本、完整 API surface、实际 used capabilities、目标计划、实际 Python 片段、观测、地图、trace、目标进度和逐次模型请求的触发原因/usage/耗时/错误。存储模型返回的任务元数据与代码，不存储思维链。token 缺失保留未知；汇总只累计已返回 usage，请求超时的服务端消耗无法推算。

每次 episode 结束立即保存原始 JSON、追加 JSONL 并更新汇总。`--resume` 仅在任务、预算、模型设置、Runtime 限额与源代码签名匹配时续跑，避免混合不同实验。

当前签名覆盖 `agent/navigation/runtime/robot/task` Python 文件与 `mission_benchmark.py`。它不是完整环境锁文件；复现还需保持 `world/providers`、依赖版本与配置一致。

## DeepSeek 正式配对结果

完整完成 10 题 × 5 次 × 3 方案，共 **150 个 episode**。其中模型方案发出 **987 次**请求，每次均返回 usage；未触发 API 读取超时。完整统计、150 条 episode 指标、目标目录与私有评分顺序、环境版本及相关源码 SHA256 见 [机器可读汇总](p3_mission_planning_summary.json)。正式实验的源代码签名为 `5891077a2f975528130f7b439971b7727b2ff459f201e72f069c28eb61fe389c`，基于提交 `ae027b858f515a96cdcc9169811e8f4da74d5810` 的本次工作区改动运行。

| 方案 | 完整任务成功 | 初始目标顺序正确 | 总耗时中位 / P95 | 平均动作数 | 模型调用总数 | 输入 / 输出 token |
|---|---:|---:|---:|---:|---:|---:|
| reference（oracle） | **50/50** | 提供正确顺序 | 2.253 / 3.335 秒 | 11.70 | 0 | — |
| hybrid（低频规划＋导航） | **50/50** | 50/50 | **3.333 / 4.720 秒** | 11.70 | **50** | **24,700 / 6,046** |
| direct（逐动作生成） | **30/50** | 50/50 | **16.644 / 167.557 秒** | 16.80 | **937** | **817,233 / 315,106** |

总耗时包含成功与失败样本的模型等待、真实 Worker 启动和执行；失败后预算耗尽的长时间等待也计入。虚拟动作本身仍即时完成，表中时间不是实车总行程时间。仅看 direct 成功的 30 个样本，其总耗时中位数为 12.386 秒、平均动作数为 5.17；这个成功子集排除了全部失败绕障任务，不能与 hybrid 的完整 50 个样本直接比较。

| 任务 | 内容 | reference | hybrid | direct |
|---|---|---:|---:|---:|
| H1 | 空场地单目标 | 5/5 | 5/5 | 5/5 |
| H2 | 前方障碍位于 x=2.0 | 5/5 | 5/5 | 0/5 |
| H3 | 前方障碍位于 x=2.7 | 5/5 | 5/5 | 0/5 |
| H4 | 前方障碍位于 x=3.2 | 5/5 | 5/5 | 0/5 |
| M1 | A→B→起点 | 5/5 | 5/5 | 5/5 |
| M2 | B→A | 5/5 | 5/5 | 5/5 |
| M3 | A→B→A，重复访问 | 5/5 | 5/5 | 5/5 |
| M4 | 只去 B，忽略其他目标 | 5/5 | 5/5 | 5/5 |
| M5 | 带障碍，A→B | 5/5 | 5/5 | 0/5 |
| M6 | 不同起点，A→出发点 | 5/5 | 5/5 | 5/5 |

三组均 **零碰撞、全部最终停稳**。hybrid/reference 均零 guard 拒绝；direct 有 3 次倒车被 guard 拒绝，均发生在最终成功的 M3 样本中，后续根据反馈转向再前进。

direct 的 20 个失败：`PolicySyntaxError` 13 次、`UnsafePolicyError` 1 次、`ModelRequestBudgetExceeded` 6 次。H2–H4 多次在障碍前正反转向，难以持续执行绕行；部分最终回复夹带说明文字，被 Python 校验拒绝。H4 第二轮累计转向 6,480°，只前进 1.95 米后安全结束。所有原始失败都保留，未修改提示词后重跑取代。

本次 hybrid 每个 episode 只请求一次任务规划，没有触发额外重规划；相较 direct，调用总数减少 **94.7%**，已记录 token 总量减少 **97.3%**。这些是请求与 usage 指标，不是账单费用。两种模型方案都能正确理解本批初始目标顺序，主要差异在局部执行的持续性与格式稳定性。

direct 在开放场地可以更省动作：M1 用 8–10 个动作，hybrid 用 20 个；M2 用 6–7 个，hybrid 用 13 个。M1 两组总耗时中位数分别为 19.875 和 4.632 秒，M2 为 13.268 和 3.773 秒。当前四邻接栅格与扫描策略仍有优化空间；后续应单独评测路径长度和运动时间，不能只凭本次软件耗时决定实车的最优路线。

**下一步采用 hybrid 作为仿真主线，direct 保留为对照组。** 先验证观测与动作误差下的可靠性，再扩展模型输入形式。本批结果支持“模型理解任务、局部层持续导航”的分工，尚不能证明真实硬件或原生视觉条件下的表现。

## 自动验证与独立地图

- `pytest`：173 passed in 13.59s。
- 原始 mock benchmark：能力 18/18，安全 2/2。
- 复杂 reference benchmark：严格判据 12/12。
- 新 benchmark 的离线参考 Provider：reference/hybrid/direct 各 10/10；所有模式均是参考实现，不能作为模型成绩。
- DeepSeek hybrid 初次检查：10/10，每题一次模型请求，全部目标顺序正确；作为正式重复评测前的检查单独保存。
- 100 个新随机可达地图（seed=314159）：100/100，零碰撞、零 guard 拒绝，平均 12.18 个动作。该批结果未用于调整导航算法。
- wheel 构建成功，检查包含新模块和 `robot-cap-mission-benchmark` 入口。
- 正式结果共 150 条、无重复或遗漏；执行完全相同参数的 `--resume`，成功跳过全部已完成 episode，没有额外模型请求。

自动测试覆盖共享地图与 command ID、累计预算、公开目标白名单、重复访问、错误顺序评分、模型超时与中止、Python 校验、动作请求和重规划上限，以及真实 spawn Worker。

## 范围与后续

当前覆盖二维静态世界、理想测距、精确位姿、预先公开的目标位置。尚未测量视觉目标定位、噪声、动作误差、动态障碍或真实车体控制。复杂死胡同探索效率、不可达判定和物理传感器 footprint 转换仍需专门验证。

语言任务是少量明确的中文目标访问指令，公开坐标不需要模型从图像定位。每题 5 次重复只能说明这些设置下的实测表现，不能据此推断开放指令或真机上的成功率；`deepseek-flash` 为服务模型别名，返回同名也不证明底层权重版本固定。

下一阶段 P4 应逐项加入测距丢读/噪声、观测延迟、定位偏差、动作误差，再加入动态障碍及占用信息过期机制。仿真真值用于独立评分，规划器使用带误差的观测。Policy Reuse 继续后置。

建议顺序与验收：

1. 测距丢读、误差与延迟：每次只启用一种扰动，使用固定种子；无效或过期读数必须安全结束，不能补成可通行区域。
2. 动作与定位误差：分开保存实际位姿和估计位姿，评测器读取真值，规划器只读取估计；检验误差积累时的到达判定和安全余量。
3. 动态障碍：观测地图需要时间戳与过期策略，再测障碍进入路径、消失及临时封路；不能直接沿用静态地图的永久占用假设。
4. 在独立种子和更长多目标任务上报告完整任务成功率、碰撞率、安全中止率、请求数、路径长度与耗时。与本次 P3 协议分开版本，不改旧 benchmark 来提升成绩。

## 文件变更

- 新增：`agent/task_planner.py`、`agent/mission.py`、`mission_benchmark.py`、`task/mission_benchmark_tasks.py`、`task/waypoint_evaluator.py`、`tests/test_mission.py`、本报告及机器可读汇总。
- 修改：`navigation/planner.py`、`navigation/runner.py`、`agent/coder.py`、`pyproject.toml`、`README.md`。
- 删除：无。`task/benchmark_tasks.py`、旧复杂任务评分器和 Runtime 核心未修改。

## 复现

设置 `LLM_BASE_URL` 和 `LLM_API_KEY` 后执行：

```bash
python mission_benchmark.py --provider openai-compatible --model deepseek-flash --reasoning-effort low --request-timeout 30 --runs 5 --output runs/p3-deepseek-paired
# 中断后使用完全相同参数并添加 --resume
python mission_benchmark.py --provider reference --output runs/p3-reference-check
python navigation_benchmark.py --random-worlds 100 --seed 314159 --output runs/p3-navigation-heldout
python benchmark.py --provider mock --output runs/p3-base-regression
python complex_benchmark.py --provider reference --output runs/p3-complex-regression
python -m pytest -q
```

本地完整记录位于上述 `runs/` 目录，原始 Policy/请求/trace 被 Git 忽略；正式对照的汇总已随本报告提交。
