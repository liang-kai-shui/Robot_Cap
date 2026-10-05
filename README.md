# Robot CaP V1 Runtime Hardening

在普通电脑上测量 Code-as-Policy 机器人 Agent 的实验项目。用户给出自然语言任务，Provider 生成 Python Policy；系统校验语法，在独立子进程运行 Policy，由可信主进程执行机器人请求，再由二维虚拟世界判定任务结果并记录指标。当前仍不连接真实硬件。

## 版本

- **V0.1**：冻结于 Git 标签 `v0.1-baseline`，用于与后续实验公平比较。
- **V0.2 Hardening**：收紧生成 Prompt 与受限 Policy DSL 的契约；保留对函数定义和 `+=`、`-=` 等增强赋值的禁止。测距按 9 位小数归一（分辨率 `1e-9` 米）；非零且小于该分辨率的 `move` 抛出 `MoveBelowResolutionError`，不计 Action。显式 `move(0)` 仍是计数、记录日志的空动作。正常小距离移动（如 `0.01` 米）不受影响。
- **V1 Runtime Foundation**：Policy Worker 只持有 `RobotProxy`；可信主进程持有 `RobotRuntime` 和 `VirtualBackend`，负责能力注册、参数与速度限制、动作分发、状态、trace 和紧急停止。保留旧 Robot API 及原有 20 个 Benchmark 任务。
- **V1 Runtime Hardening**：能力成为运行时可注册的 `CapabilitySpec + handler`。Canonical ID 与公开调用路径分离；Prompt、Validator、Proxy 和 Runtime 使用同一 Registry。动作超时会请求协作式取消并触发紧急停止。

V1 保存未来 Policy reuse 所需的 episode 数据，但不检索或复用历史 Policy；也不实现视觉、音频、机械臂或 ESP32 驱动。冻结版本的真实模型结果保留作对比。

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
```

参考 `.env.example`。环境变量需由 shell 或部署环境设置；项目不自动读取 `.env`。该 Provider 默认使用非流式 `/chat/completions`，此时 `llm_ttft_ms` 为 `null`。使用 `--stream` 会按首个非空 Policy 文本片段记录 TTFT，并请求末尾 usage 块；兼容服务若不支持 `stream_options`，可加 `--no-stream-usage`。未返回的 token usage 保持 `null`，V0 不估算缺失指标。

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

## Metrics 与运行记录

每次运行都写入 `runs/` 的 JSON，包括任务、Policy、初末状态、兼容旧调用的 Robot API 日志、异常、`RunMetrics` 和 `episode`。Episode 包含 `api_version="v1"`、本次暴露的完整 `api_surface_signature`、从 trace 提取的实际 `used_capabilities`、执行与任务结果及指标；默认 Benchmark 不读取 episode，也不进行 Policy reuse。Trace 记录 `started`、`completed`、`failed` 或 `cancelled`、观测及紧急停止事件，并附稳定的 `capability_id`、command ID、请求参数、结果或错误及可用的状态快照。

`llm_total_ms` 记录请求到完整响应；流式模式下 `llm_ttft_ms` 记录请求到首个非空 Policy 片段；`validation_ms` 为 AST 校验；`execution_ms` 从 Worker 启动到结束或终止；`evaluation_ms` 为任务判定；`total_ms` 从任务开始到结果生成，但排除 CLI 显示 Policy 与用户确认耗时。`presentation_ms` 和 `confirmation_wait_ms` 分别记录这两段时间；无需确认时后者为 `null`。读取姿态、状态和距离不增加 Action 数；`move`、`turn`、`stop` 增加 Action 数。

## 安全模型与限制

V1 的 Worker 与 Backend 有进程边界，但 AST 白名单、受限 builtins 和 `spawn` 子进程仍不能替代操作系统级沙箱；不要将其视为可安全运行任意恶意 Python 的环境。Policy 计算超时累计 worker 执行 Python 的时间，在可信 Runtime 已接受的阻塞调用期间暂停；无限计算仍会被终止并紧急停止。通信超时只限制请求确认及结果传输，不限制已确认动作的正常执行时间。Action deadline 由 Runtime 独立控制；超时会设置 `cancel_event`，随后调用 Backend 的 `emergency_stop()` 并返回 `RobotActionTimeoutError`。这是协作式取消：Python 不能安全杀死运行中的线程。未来物理 handler 必须使用有界 I/O、检查取消信号和 deadline，且紧急停止必须独立于普通动作锁；不遵守契约的 handler 不能安全接入真机。

世界使用点机器人和轴对齐矩形障碍物，不含动力学、机器人半径或物理引擎。普通文本任务若无结构化成功条件，无法客观证明任务完成。OpenAI Compatible Provider 的网络端点需自行配置；本项目的离线 Mock 测试不验证外部 API 可用性。

## 测试

```bash
python -m pytest
python benchmark.py --provider mock
```

测试覆盖世界、碰撞、测距、Robot API、任务判定、AST 拒绝、Worker/Backend 隔离、动态能力注册与注销、路径冲突、三种超时的交互、取消与紧急停止、速度限制、command ID 幂等、trace、episode、Mock 端到端链路、Metrics 和 JSON 落盘。

## Roadmap

后续可在独立阶段实现硬件 Backend、设备级停止、感知能力和 Policy reuse。当前版本只提供扩展接口与可比较的运行记录。
