# DeepSeek Flash 全量复测

日期：2026-10-06（北京时间）

代码：`v1-runtime-hardening` / `2eda7fd5c3589c752864f8b554ce5e9aa9ebd9de`

接口模型名：`deepseek-flash`

本次按现有入口重跑全部原始任务、复杂已知地图任务和隐藏地图交互任务，每题 3 次；另复测 P1/P2 的全部固定及固定种子导航场景。导航器当前不调用模型，因此其结果单独列出。接口连通性检查 A1 运行 1 次，**不计入**以下统计。

## 配置和可比性

- DeepSeek Chat Completions，非流式；单次 API socket/read 超时 30 秒。基础和复杂组维持服务默认思考设置；交互组使用 `reasoning_effort=low`，最多 8 次决策。模型别名实际指向的服务版本未在结果中证明固定。
- 基础组 A1–D4 是 18 类模型生成任务，共 54 次。E1/E2 是固定 Policy 的安全测试，共 6 次；它们不调用模型，也不计入能力成功率。
- 复杂组 12 类任务共 36 次，输入包含完整地图。分别报告最终目标成功和额外的 trace 严格判据。
- 交互组 H1–H4 共 12 次，输入只含局部位姿、前向测距和近期反馈；不提供完整地图。P1 新增的结构化测距字段也随局部观测传入，但尚未把 P2 的确定性导航器接到模型决策链。
- 导航组 12 个固定场景（11 可达、1 不可达）以及种子 `20261006` 的 20 个可达场景，预算为 80 个动作和 160 次观测。它们不属于 DeepSeek 成绩，不能与交互组 8 次决策直接比较。
- token 只统计 API 成功返回 usage 的请求。读取超时请求的 token 和实际账单费用未知。本报告不推算费用。

## 总结果

| 评测 | 完成结果 | 更严格结果 | 总耗时中位 / P95 | 模型调用 | 已记录输入 / 输出 token |
|---|---:|---:|---:|---:|---:|
| 基础能力任务 | **54/54** | — | 1.235 / 5.023 秒 | 54 | 29,397 / 14,163 |
| 基础安全测试 | **6/6** | — | 单独固定 Policy | 0 | — |
| 复杂已知地图 | **33/36** 最终目标 | **23/36** trace 判据 | 11.994 / 28.338 秒 | 36 | 20,643 / 91,778 |
| 隐藏地图交互，`low` | **7/12** | — | 27.710 / 42.422 秒 | 74 | 64,683 / 42,360 |
| P1/P2 固定可达场景 | **11/11** | 不可达场景 1/1 安全停稳 | 2.416 / 13.530 秒（含不可达场景） | 0 | — |
| P1/P2 随机场景 | **20/20** | 零碰撞 | 1.913 / 3.863 秒 | 0 | — |

三组模型评测共发出 **164 次**模型请求。其中复杂组 2 次、交互组 1 次触发 30 秒读取超时，无法获得对应请求的 usage；表中的 token 总和不包含这些未知消耗。固定导航和随机导航均无 guard 拒绝。`python -m pytest -q` 本次实际结果为 **152 passed in 11.00s**。

## 逐任务结果

### 基础能力与安全

A1–A5、B1–B5、C1–C4、D1–D4 各 **3/3** 完成，54 次无碰撞、验证失败或 Policy 执行超时。E1 的 `UnsafePolicyError`、E2 的 `PolicyTimeoutError` 各 3/3 符合预期。

### 复杂已知地图

| 任务 | 最终目标 | 严格 trace | 主要记录 |
|---|---:|---:|---|
| N1 | 2/3 | 0/3 | 1 次 API 读取超时；另外 2 次检查点顺序未命中 |
| N2 | 3/3 | 0/3 | 3 次检查点顺序未命中 |
| N3、N4 | 各 3/3 | 各 3/3 | 通过 |
| F1、F2 | 各 3/3 | 各 3/3 | 通过 |
| AS1 | 3/3 | 2/3 | 1 次检查点顺序未命中 |
| AS2 | 2/3 | 1/3 | 1 次 API 读取超时，1 次测距次数不足 |
| SP1、SP2 | 各 3/3 | 各 3/3 | 通过 |
| MX1 | 2/3 | 0/3 | 1 次 `MoveLimitExceeded`；另外 2 次检查点顺序未命中 |
| MX2 | 3/3 | 2/3 | 1 次移动前测距判据未命中 |

严格判据按类别：navigation 6/12、feedback 6/6、active sensing 3/6、speed 6/6、mixed 2/6。`waypoint_order` 在 11 次运行中未满足，是最多的失败项。当前评分器只在每次 `move` 结束的位姿上检查 waypoint；路线穿过而未在 waypoint 附近结束一个动作，也可能被记为失败。因此最终目标成功率和严格 trace 成绩必须并列保留。3 次最终目标失败分别是 N1/AS2 的模型读取超时、MX1 的移动限额错误。

### 隐藏地图交互

| 任务 | 成功 | 失败原因 |
|---|---:|---|
| H1 | 3/3 | — |
| H2 | 2/3 | 1 次决策次数耗尽 |
| H3 | 1/3 | 1 次决策次数耗尽，1 次模型读取超时 |
| H4 | 1/3 | 2 次决策次数耗尽 |

共 7/12 成功、0 碰撞、0 guard 拒绝。H2-2、H3-1、H3-2 等失败轨迹多次在相近位置反复 `turn(90)`、`turn(-90)`，消耗有限决策预算；H3-2 还在第 7 次请求读超时。P1 的结构化观测本身没有消除这种模型规划循环。

### P1/P2 导航复测

固定可达场景 H1–H4、G1–G3、W1–W2、U1、C1 全部完成；动作数依次为 2、16、14、14、11、10、4、30、49、78、4。X1 不可达场景在 80 次动作预算耗尽后停稳，不等于系统已证明目标全局不可达。20 个固定种子随机可达场景全部完成，动作数与上轮相同；两组均零碰撞。

这些结果验证当前确定性导航执行链复测稳定。它使用公开目标、观测地图和 A* / 边界探索，且动作预算与模型交互不同；**不能据此声称 DeepSeek 已能完成这 31 个可达导航场景**。

## 与此前实测比较

| 相同测试条件 | 此前 | 本次 | 判断 |
|---|---:|---:|---|
| 基础能力任务 | 54/54 | 54/54 | 保持通过 |
| 复杂任务最终目标 | 32/36 | 33/36 | 相差 1 次，样本不足以证明改进 |
| 复杂任务严格 trace | 25/36 | 23/36 | 检查点、测距等判据仍不稳定 |
| 最终交互版 `low` | 5/12 | 7/12 | 多 2 次，H3/H4 仍只有各 1/3 |
| 最终交互版 `low` 中位总耗时 | 24.699 秒 | 27.710 秒 | 本次略慢；请求和服务波动均会影响耗时 |

以前的 `low` 交互结果见 [交互优化报告](interactive_optimization_2026-10-05.md)。复杂组与基础组旧结果见 [此前全量报告](deepseek_flash_2026-10-05.md)。这次代码已经包含 P1/P2 观测变更，且模型请求不是固定种子实验；差异不能单独归因于代码。小样本成功率不代表稳定部署能力。

## 判断与下一步

基础动作链路稳定，但当前模型驱动的隐藏地图任务仍受重复转向、有限决策预算和模型读取超时影响。最有价值的下一项软件实验是按既定 P3 方案，让模型低频选择**公开的目标或子目标**，再由已验证的 P2 确定性层处理局部观测、路径和安全动作。应在相同世界、目标、预算和重复次数下报告成功率、模型调用、token、端到端耗时及动作数；必要时分别比较模型请求 30/60 秒读超时。此报告只提出下一步，不包含 P3 实现，也没有启动 Policy Reuse。

## 复现与记录

设置 `LLM_BASE_URL=https://api.deepseek.com`、`LLM_API_KEY`、`LLM_REQUEST_TIMEOUT_SECONDS=30` 后，在仓库根目录运行：

```bash
python benchmark.py --provider openai-compatible --model deepseek-flash --runs 3 --output runs/deepseek-full-rerun/base
python complex_benchmark.py --provider openai-compatible --model deepseek-flash --runs 3 --output runs/deepseek-full-rerun/complex
python interactive_benchmark.py --provider openai-compatible --model deepseek-flash --reasoning-effort low --request-timeout 30 --runs 3 --output runs/deepseek-full-rerun/interactive-low
python navigation_benchmark.py --output runs/deepseek-full-rerun/navigation-fixed
python navigation_benchmark.py --random-worlds 20 --seed 20261006 --output runs/deepseek-full-rerun/navigation-seeded
python -m pytest -q
```

本地 `runs/deepseek-full-rerun/` 保存全部逐次 JSON、trace、生成的 Policy、`raw_results.jsonl` 和各组 `summary.json`。这些原始记录被 Git 忽略；提交的 [机器可读汇总](deepseek_full_rerun_summary.json) 包含逐题成功次数、主要错误、token 与耗时统计，不含 API Key。
