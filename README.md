# Robot CaP V0

在普通电脑上测量 Code-as-Policy 机器人 Agent 的最小实验项目。用户给出自然语言任务，Provider 生成 Python Policy；系统校验语法、在独立子进程运行，再由二维虚拟世界判定任务结果并记录指标。V0 不连接真实硬件。

## 版本

- **V0.1**：冻结于 Git 标签 `v0.1-baseline`，用于与后续实验公平比较。
- **V0.2 Hardening**：收紧生成 Prompt 与受限 Policy DSL 的契约；保留对函数定义和 `+=`、`-=` 等增强赋值的禁止。测距按 9 位小数归一（分辨率 `1e-9` 米）；非零且小于该分辨率的 `move` 抛出 `MoveBelowResolutionError`，不计 Action。显式 `move(0)` 仍是计数、记录日志的空动作。正常小距离移动（如 `0.01` 米）不受影响。

V0.2 不新增 Policy reuse、Replan、Skill Library、Vision 或 ESP32，也不改变固定 Benchmark 任务。冻结版本的真实模型结果保留作对比；本版本的代码修改仅进行离线验证。

## 架构

```text
Natural Language Task → LLMProvider → Python Policy → AST Validator
                                               ↓
                                Spawned Worker / Restricted Globals
                                               ↓
                                  RobotBase → VirtualRobot → VirtualWorld
                                               ↓
                                     TaskEvaluator → Result + Metrics
```

Agent 只依赖稳定的 Robot API。`TaskEvaluator` 在 Worker 返回状态后独立判断任务完成情况；`execution_success` 和 `task_success` 是不同字段。Provider 可以替换，Robot API 上层不需要知道机器人具体实现。

## 安装

要求 Python 3.11+。

```bash
python -m venv .venv
# 激活虚拟环境后：
python -m pip install -e ".[dev]"
pytest
```

也可执行 `python -m pip install -r requirements.txt` 并从项目根目录运行脚本。Windows PowerShell 可使用 `.venv\Scripts\Activate.ps1` 激活环境。

## 配置与环境变量

`config/default.yaml` 定义世界大小、起始姿态、矩形障碍物与 Runtime 限额。角度采用 `[0, 360)`；0° 指向 +X，90° 指向 +Y。前进距离和世界坐标单位为米。

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

Robot API：`move(distance)`、`turn(angle)`、`stop()`、`get_pose()`、`get_distance()`、`get_state()`。`move` 和 `turn` 的正负方向遵循需求文档。`get_distance()` 返回当前方向上最近障碍物或世界边界的距离。

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

每次运行都写入 `runs/` 的 JSON，包括任务、Policy、初末状态、Robot API 日志、异常和 `RunMetrics`。`llm_total_ms` 记录请求到完整响应；流式模式下 `llm_ttft_ms` 记录请求到首个非空 Policy 片段；`validation_ms` 为 AST 校验；`execution_ms` 从 Worker 启动到结束或终止；`evaluation_ms` 为任务判定；`total_ms` 从任务开始到结果生成，但排除 CLI 显示 Policy 与用户确认耗时。`presentation_ms` 和 `confirmation_wait_ms` 分别记录这两段时间；无需确认时后者为 `null`。读取姿态、状态和距离会记日志，但不增加 Action 数；`move`、`turn`、`stop` 增加 Action 数。

## 安全模型与限制

V0 Runtime 仅用于运行受约束、由可信模型生成的实验 Policy，不适合作为不受信任代码的安全执行环境。它使用 AST 白名单、受限 builtins、独立 `spawn` 子进程、超时、Action 上限及单次 API 限额；不能替代操作系统级沙箱。Worker 超时后会被终止，必要时强制杀死。超时发生时无法回传 Worker 的中途状态，结果使用初始状态；正常异常会回传最终状态和日志。

世界使用点机器人和轴对齐矩形障碍物，不含动力学、机器人半径或物理引擎。普通文本任务若无结构化成功条件，无法客观证明任务完成。OpenAI Compatible Provider 的网络端点需自行配置；本项目的离线 Mock 测试不验证外部 API 可用性。

## 测试

```bash
pytest
```

测试覆盖世界、碰撞、测距、Robot API、任务判定、AST 拒绝、独立 Worker、死循环终止、Action 上限、Mock 端到端链路、Metrics 和 JSON 落盘。

## Roadmap

先用 V0 固定任务和真实 Provider 建立基线，再依据成功率、延迟和失败类型决定下一步。可选后续方向依次为二维可视化、反馈修补、Policy 复用以及真实机器人和感知接入；这些均不属于 V0。
