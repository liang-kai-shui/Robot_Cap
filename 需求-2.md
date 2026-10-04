# Robot CaP V0 最终需求文档

## 1. 项目名称

暂定：**Low-Cost Embodied Coding Agent / Robot CaP V0**

V0 不接任何真实硬件，目标是在普通电脑上完成一套可运行、可测试、可测量延迟的 **Code-as-Policy（CaP）机器人 Agent 最小原型**。

V0 的目的不是做完整机器人系统，而是验证以下核心假设：

> 用户输入自然语言任务后，快速 LLM 能根据固定 Robot API 动态生成 Python Policy；系统可以安全地校验和执行该 Policy；Policy 能控制一个二维虚拟机器人完成任务；系统可以客观记录成功率、延迟、Token、执行行为等指标，为后续真实机器人和 Policy 复用提供 baseline。

## 2. V0 核心链路

```text
Natural Language Task
        ↓
    LLM Provider
        ↓
   Python Policy
        ↓
   AST Validator
        ↓
 Isolated Executor
  (子进程执行)
        ↓
    RobotBase API
        ↓
   VirtualRobot
        ↓
   VirtualWorld
        ↓
  Task Evaluator
        ↓
 Execution Result
 + Task Result
 + Metrics
```

V0 必须完成这条链路，并能重复稳定运行。

## 3. V0 核心目标

V0 必须实现：

1. 用户可以输入自然语言机器人任务。
2. 系统调用可配置 LLM。
3. LLM 根据明确提供的 Robot API 生成 Python Policy。
4. 系统提取生成代码并进行 AST 静态安全检查。
5. Policy 在独立子进程中执行。
6. Policy 只能访问受限 Robot API。
7. VirtualRobot 可以在二维世界中移动、旋转、读取状态和测距。
8. VirtualWorld 支持边界、矩形障碍物和基础碰撞检测。
9. Runtime 能限制执行时间、Action 数量、单次移动距离、单次旋转角度。
10. 系统必须区分 Policy 是否正常执行完成，与用户任务是否真的完成。
11. 系统必须记录完整 Metrics。
12. 系统提供固定 Benchmark Tasks，可以比较不同 LLM 的成功率、延迟、Token、Policy 长度、Action 次数和失败类型。
13. 项目必须保留清晰抽象，使未来可以把 `VirtualRobot` 替换为 `ESP32Robot`，而 Agent 层无需大改。

## 4. V0 明确不实现的内容

为了保证 V0 小而稳定，以下功能禁止提前实现：

```text
ESP32
真实电机
WebSocket 硬件通信
摄像头
VLM
OpenCV
SLAM
ROS / ROS2
麦克风
TTS
树莓派 / Linux SBC
Policy Retrieval
Policy Memory
Policy Reuse
Policy Patch
Skill Library
Skill Synthesis
Embedding
向量数据库
自动 Replan
Reflection Loop
Multi-Agent
强化学习
模型训练
自动修改自身源码
复杂物理仿真
Docker / gVisor Sandbox
Web UI
```

这些功能属于 V0.x、V1、V2、V3 及之后版本。

## 5. 技术要求

### 5.1 语言

Python 3.11+。

### 5.2 推荐依赖

优先标准库。可以使用：

```text
pydantic
pyyaml
httpx
python-dotenv
rich
pytest
```

如需流式 API 客户端，可增加必要依赖，但不得引入大型 Agent Framework。

### 5.3 禁止依赖

V0 不允许直接引入：

```text
LangChain
AutoGen
CrewAI
ROS
复杂仿真框架
大型 RL 框架
```

核心 Agent Loop、Runtime、Validator、Robot API 由项目自己实现。

## 6. 建议项目目录

```text
robot-cap/
│
├── main.py
├── benchmark.py
├── agent/
│   ├── __init__.py
│   ├── coder.py
│   ├── prompts.py
│   └── model.py
│
├── robot/
│   ├── __init__.py
│   ├── base.py
│   ├── virtual.py
│   └── state.py
│
├── runtime/
│   ├── __init__.py
│   ├── executor.py
│   ├── worker.py
│   ├── validator.py
│   ├── limits.py
│   └── result.py
│
├── world/
│   ├── __init__.py
│   ├── world.py
│   ├── obstacle.py
│   └── geometry.py
│
├── task/
│   ├── __init__.py
│   ├── task.py
│   ├── evaluator.py
│   └── benchmark_tasks.py
│
├── providers/
│   ├── __init__.py
│   ├── base.py
│   ├── openai_compatible.py
│   └── mock.py
│
├── metrics/
│   ├── __init__.py
│   ├── collector.py
│   └── models.py
│
├── config/
│   └── default.yaml
│
├── runs/
│   └── .gitkeep
│
├── tests/
│   ├── test_virtual_robot.py
│   ├── test_world.py
│   ├── test_validator.py
│   ├── test_executor.py
│   ├── test_task_evaluator.py
│   └── test_metrics.py
│
├── .env.example
├── requirements.txt
└── README.md
```

允许合理调整，但必须保持以下分层：

```text
Agent
↓
Runtime
↓
Robot API
↓
VirtualRobot
↓
World
```

并保持 `Task Evaluator` 与 `Metrics` 和执行逻辑解耦。

## 7. Robot API

Robot API 是整个项目最重要的稳定接口。必须定义抽象基类：

```python
class RobotBase:
    def move(self, distance: float) -> None:
        ...

    def turn(self, angle: float) -> None:
        ...

    def stop(self) -> None:
        ...

    def get_pose(self) -> "Pose":
        ...

    def get_distance(self) -> float:
        ...

    def get_state(self) -> "RobotState":
        ...
```

未来新增真实机器人时应实现：

```python
class ESP32Robot(RobotBase):
    ...
```

上层 Agent 不得依赖具体实现类型。

## 8. Robot 数据结构

### 8.1 Pose

```python
class Pose:
    x: float
    y: float
    heading: float
```

统一约定：

```text
heading = 0°   → +X 方向
heading = 90°  → +Y 方向
```

角度归一化方案任选 `[-180, 180)` 或 `[0, 360)`，但项目全局必须一致。

### 8.2 RobotState

至少包含：

```python
class RobotState:
    pose: Pose
    stopped: bool
    collision: bool
    last_action: str | None
    action_count: int
```

## 9. Robot API 语义

### 9.1 `move(distance)`

机器人沿当前 heading 移动。

```python
robot.move(1.0)   # 前进 1 米
robot.move(-0.5)  # 后退 0.5 米
```

必须处理世界边界、障碍物、Action 限制和最大单次移动距离。非法运动应抛出明确异常。

### 9.2 `turn(angle)`

原地旋转。约定：

```text
正数 = 逆时针
负数 = 顺时针
```

例如：

```python
robot.turn(90)
robot.turn(-45)
```

必须处理最大单次旋转限制。

### 9.3 `stop()`

V0 中主要用于语义完整、日志记录和未来接口兼容。未来真实机器人版本中将成为安全关键 API。

### 9.4 `get_pose()`

返回当前 Pose。

### 9.5 `get_distance()`

返回机器人当前 heading 方向上，到最近障碍物或世界边界的距离。V0 使用二维射线几何计算即可。

### 9.6 `get_state()`

返回完整 RobotState。

## 10. VirtualWorld

V0 使用二维平面：

```text
y
↑
│
│        ███
│        ███
│
│  🤖 →
│
└────────────────→ x
```

配置文件示例：

```yaml
world:
  width: 10.0
  height: 10.0

robot:
  x: 1.0
  y: 1.0
  heading: 0.0

obstacles:
  - x: 4.0
    y: 3.0
    width: 1.0
    height: 2.0
```

V0 不需要物理引擎。必须支持二维坐标、heading、矩形障碍物、边界、基础碰撞检测和射线距离检测。

## 11. Task 模型

V0 必须正式区分 `Execution Success` 和 `Task Success`。

例如任务：

```text
向前走 2 米
```

模型生成：

```python
robot.stop()
```

此时：

```text
execution_success = true
task_success = false
```

## 12. Task 数据结构

建议：

```python
class Task:
    id: str
    instruction: str
    initial_state: ...
    success_spec: ...
```

不要直接在配置文件中保存任意 Python lambda。推荐使用结构化 success spec。

例如：

```yaml
id: move_forward_2m
instruction: 向前走2米

initial:
  x: 1.0
  y: 1.0
  heading: 0.0

success:
  type: pose_near
  x: 3.0
  y: 1.0
  tolerance: 0.05
```

条件停止任务可使用：

```yaml
success:
  type: distance_range
  min: 0.0
  max: 0.5
```

## 13. Task Evaluator

必须实现独立 `TaskEvaluator`：

```text
Policy Executor
↓
Final Robot State
↓
TaskEvaluator
↓
task_success
```

V0 至少支持以下成功条件类型：

```text
pose_near
heading_near
distance_range
stopped
compound_all
```

如需要可增加 `compound_any`。

## 14. LLM Provider 抽象

禁止把某家 API 写死。定义统一接口，例如：

```python
class LLMProvider:
    def generate_policy(
        self,
        task: str,
        robot_api: str,
        world_state: str,
    ) -> "LLMResponse":
        ...
```

其中：

```python
class LLMResponse:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    ttft_ms: float | None
    total_ms: float
```

## 15. Provider 实现

V0 至少实现：

```text
MockProvider
OpenAICompatibleProvider
```

OpenAI-Compatible Provider 使用：

```env
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
```

如果 Provider 不支持某项 Metrics，则对应字段设为 `null`，禁止估算或伪造。

## 16. Prompt 设计

System Prompt 必须明确告诉模型：

1. 当前任务。
2. Robot API。
3. 当前环境状态。
4. 只能返回 Python Policy。
5. 不允许 import。
6. 不允许文件操作。
7. 不允许网络操作。
8. 不允许 shell。
9. 不允许访问任意未知对象。
10. 只允许使用 Robot API 和有限基础语言结构。
11. Policy 应尽量短。
12. 任务结束应调用 `robot.stop()`。
13. 不得输出 Markdown code fence。

建议核心 Prompt：

```text
You are a robot policy coding agent.

Generate Python code that controls the provided `robot` object.

You may ONLY use the documented Robot API.

Do not import modules.
Do not access files.
Do not access network.
Do not use eval, exec, open, compile, globals, locals, getattr, setattr, subprocess, os, sys, socket or pathlib.

Return executable Python code only.
Do not use Markdown fences.
Keep the policy concise.
Stop the robot when the task is complete.
```

## 17. Policy 示例

用户：

```text
向前走两米，左转90度，再向前走一米。
```

理想输出：

```python
robot.move(2.0)
robot.turn(90)
robot.move(1.0)
robot.stop()
```

用户：

```text
一直向前走，每次0.1米，距离障碍物小于0.5米时停止。
```

理想输出：

```python
while robot.get_distance() >= 0.5:
    robot.move(0.1)

robot.stop()
```

## 18. Policy 提取

LLM 有时可能仍返回 Markdown code fence。V0 可以做简单清洗，例如移除 code fence 和前后解释文字，但不要做过于复杂的启发式 parser。

如果无法可靠提取 Python，记录 `PolicyGenerationError`。

## 19. AST Policy Validator

LLM 代码绝对禁止未经检查直接执行。使用 Python `ast`。

### 19.1 允许语法

允许：

```text
赋值
数字
字符串
布尔值
简单算术
比较
if
while
for
break
continue
range
min
max
abs
Robot API 调用
```

### 19.2 允许 Robot API

白名单：

```text
robot.move
robot.turn
robot.stop
robot.get_pose
robot.get_distance
robot.get_state
```

调用 `robot.fly()` 等未知方法必须拒绝。

### 19.3 禁止

至少禁止：

```text
Import
ImportFrom
lambda
class
function definition
async
await
yield
with
try
raise
global
nonlocal
```

同时禁止：

```text
__import__
open
eval
exec
compile
globals
locals
vars
dir
getattr
setattr
delattr
input
help
breakpoint
```

禁止模块 / 名称：

```text
os
sys
subprocess
socket
pathlib
requests
httpx
shutil
pickle
marshal
ctypes
multiprocessing
threading
```

禁止任何 dunder 属性访问，例如：

```python
robot.__class__
robot.__dict__
().__class__
```

任何属性名以 `__` 开头都应拒绝。

## 20. Runtime 安全模型

V0 不宣称提供真正的恶意代码安全 Sandbox。

安全措施只是：

```text
AST 白名单
Restricted Globals
独立 Worker Process
执行超时
Action Limit
Robot API Limit
```

README 必须明确：

> V0 Runtime 仅用于运行受约束、由可信模型生成的实验 Policy，不适合作为不受信任代码的安全执行环境。

## 21. 独立子进程执行

这是 V0 必须实现的关键点。禁止在主进程直接 `exec(policy)`。

必须：

```text
Main Process
    ↓ spawn
Policy Worker Process
    ↓
exec(policy)
```

主进程负责启动 Worker、等待结果、超时计时、终止卡死 Worker和收集结果。

## 22. Worker Process

建议使用 `multiprocessing`，优先使用 `spawn`。

Policy Worker 仅允许访问：

```python
safe_globals = {
    "__builtins__": {
        "range": range,
        "min": min,
        "max": max,
        "abs": abs,
    },
    "robot": robot,
}
```

不要直接暴露完整 builtins。

## 23. 状态返回

因为 Worker 在独立进程中执行，必须明确解决 Robot 状态回传。

V0 推荐方案：Worker 内独立创建 VirtualRobot，结束后返回：

```text
final_state
logs
exception
action_count
```

V0 不要求 IPC Proxy。

## 24. Runtime Limits

配置示例：

```yaml
runtime:
  timeout_seconds: 5
  max_actions: 100
  max_move_distance: 2.0
  max_turn_angle: 180.0
```

### 24.1 Timeout

例如：

```python
while True:
    pass
```

必须：

```text
Worker timeout
↓
terminate()
↓
PolicyTimeoutError
```

主程序不得被卡死。

### 24.2 Action Limit

例如：

```python
while True:
    robot.move(0.01)
```

超过 `max_actions` 后必须停止。

### 24.3 API Limits

例如：

```python
robot.move(999)
```

应抛出 `MoveLimitExceeded`。

## 25. 标准异常

建议定义：

```text
UnsafePolicyError
PolicySyntaxError
PolicyTimeoutError
ActionLimitExceeded
MoveLimitExceeded
TurnLimitExceeded
CollisionError
WorldBoundaryError
PolicyExecutionError
PolicyGenerationError
```

异常最终统一进入 `ExecutionResult`。

## 26. ExecutionResult

建议：

```python
class ExecutionResult:
    execution_success: bool
    task_success: bool

    error_type: str | None
    error_message: str | None

    initial_state: RobotState
    final_state: RobotState

    logs: list
    metrics: RunMetrics
```

## 27. 执行日志

每个 Robot API 调用必须记录。

例如：

```text
[12:01:04.112] MOVE distance=1.0
[12:01:04.113] POSE x=2.0 y=1.0 heading=0.0

[12:01:04.115] TURN angle=90
[12:01:04.115] POSE x=2.0 y=1.0 heading=90.0

[12:01:04.117] STOP
```

日志必须可供 CLI、Benchmark 和未来 Replan 共同复用。

## 28. Metrics

Metrics 是 V0 正式功能，不是可选项。每次 Run 都需要生成结构化记录。

建议：

```python
class RunMetrics:
    model: str | None

    llm_total_ms: float | None
    llm_ttft_ms: float | None

    input_tokens: int | None
    output_tokens: int | None

    policy_chars: int
    policy_lines: int

    validation_ms: float
    execution_ms: float
    evaluation_ms: float
    total_ms: float

    action_count: int

    execution_success: bool
    task_success: bool

    error_type: str | None
```

## 29. Metrics 时间定义

统一：

```text
llm_total_ms
= 发出 LLM 请求 → 完整响应返回

llm_ttft_ms
= 发出请求 → 第一个 token 到达

validation_ms
= 开始 AST 验证 → 验证完成

execution_ms
= Worker 启动 → Policy 执行结束/被终止

evaluation_ms
= TaskEvaluator 开始 → 判定完成

total_ms
= 本次 Task 从开始处理 → 最终结果生成
```

如果 Provider 不支持流式，则 `llm_ttft_ms = null`。

## 30. Run 记录持久化

每次运行应保存一个 JSON 到 `runs/`。

示例：

```text
runs/
└── 2026-10-04T153000_move_forward_2m.json
```

结构示例：

```json
{
  "task_id": "move_forward_2m",
  "instruction": "向前走2米",
  "model": "example-model",
  "policy": "robot.move(2.0)\nrobot.stop()",
  "result": {
    "execution_success": true,
    "task_success": true,
    "error_type": null
  },
  "metrics": {
    "llm_total_ms": 724,
    "llm_ttft_ms": 183,
    "input_tokens": 914,
    "output_tokens": 76,
    "validation_ms": 1.4,
    "execution_ms": 3.7,
    "evaluation_ms": 0.3,
    "total_ms": 731,
    "action_count": 2
  }
}
```

## 31. CLI

运行：

```bash
python main.py
```

显示：

```text
Robot CaP V0

Task > 向前走两米然后左转90度

Generating policy...

Generated Policy:
-----------------
robot.move(2)
robot.turn(90)
robot.stop()

Validation:
PASS

Execute? [Y/n]
```

执行后显示最终状态、`Execution Success`、`Task Success` 和 Metrics。

## 32. CLI 参数

至少支持：

```text
--auto
```

建议支持：

```text
--provider
--model
--config
```

例如：

```bash
python main.py --provider openai-compatible --model example-model
```

## 33. Mock Provider

必须实现 `MockProvider`。

无 API Key 时也可以运行。Mock 不需要理解任意语言，只需支持固定测试输入，例如：

```text
向前走1米
向前走2米
左转90度
```

用途：单元测试、CI、离线开发、Runtime 调试。

## 34. Benchmark

V0 必须提供：

```bash
python benchmark.py
```

可以批量执行固定任务。

## 35. Benchmark Tasks

至少准备约 20 个固定任务，建议分组：

```text
A. Basic Motion            5
B. Sequential Composition  5
C. Conditional Logic       4
D. Feedback Loop           4
E. Safety / Invalid Code   2
```

### A. Basic Motion

```text
A1 向前走1米
A2 向后走0.5米
A3 左转90度
A4 右转45度
A5 向前走2米并停止
```

### B. Sequential Composition

例如：

```text
B1 前进2m → 左转90° → 前进1m
B2 前进1m → 右转90° → 前进1m
B3 左转180° → 前进0.5m
B4 前进0.5m → 左转45° → 前进0.5m
B5 多步组合任务
```

### C. Conditional Logic

例如：

```text
如果前方距离小于1米就停止，否则前进0.5米。
```

### D. Feedback Loop

例如：

```text
一直向前走，每次0.1米，直到距离障碍物小于0.5米。
```

### E. Safety

测试诱导 `import os` 和无限循环。此类测试可使用 MockProvider、专门恶意 Provider 或固定 Policy，无需依赖真实 LLM。

## 36. Benchmark 重复次数

Benchmark 应支持：

```bash
python benchmark.py --runs 5
```

表示每个任务运行 5 次，以观察模型输出波动。

## 37. Benchmark 汇总指标

至少输出：

```text
Task Success Rate
Execution Success Rate
Validation Failure Rate
Timeout Rate
Collision Rate
Median LLM Latency
P95 LLM Latency
Median Total Latency
P95 Total Latency
Average Input Tokens
Average Output Tokens
Average Policy Lines
Average Action Count
```

如果可以计算 API 成本，Provider 可选提供 `estimated_cost`；若价格未知，不要硬编码错误价格。

## 38. Benchmark 结果输出

建议保存：

```text
runs/benchmark/
├── raw_results.jsonl
├── summary.json
└── summary.csv   # 可选
```

V0 不要求画图。

## 39. 单元测试要求

### Robot

```text
test_move_forward
test_move_backward
test_turn
test_pose
test_world_boundary
test_collision
test_get_distance
```

### World

```text
test_obstacle_intersection
test_ray_distance
test_boundary_distance
```

### Validator

```text
test_allow_robot_move
test_allow_assignment
test_allow_if
test_allow_loop
test_allow_range
test_reject_import
test_reject_open
test_reject_eval
test_reject_exec
test_reject_dunder
test_reject_unknown_robot_method
test_reject_unknown_global
```

### Executor

```text
test_execute_simple_policy
test_action_limit
test_timeout
test_collision_result
test_worker_is_terminated
test_main_process_survives_infinite_loop
```

### Task Evaluator

```text
test_pose_near_success
test_pose_near_failure
test_heading_near
test_distance_range
test_compound_all
```

### Metrics

```text
test_metrics_created
test_metrics_null_when_provider_does_not_support_usage
test_run_json_written
```

所有测试必须通过：

```bash
pytest
```

## 40. 集成测试

至少实现以下整条链路测试：

```text
MockProvider
↓
Agent
↓
Validator
↓
Executor
↓
VirtualRobot
↓
TaskEvaluator
↓
Metrics
```

## 41. README

README 至少包含：

1. 项目简介
2. V0 目标
3. 架构图
4. 安装
5. 配置
6. 环境变量
7. CLI 用法
8. Benchmark 用法
9. 示例任务
10. Metrics 说明
11. 安全模型说明
12. 当前限制
13. Roadmap

## 42. README 架构图

建议：

```text
Natural Language
      ↓
  LLM Provider
      ↓
 Python Policy
      ↓
 AST Validator
      ↓
Worker Process
      ↓
   RobotBase
      ↓
 VirtualRobot
      ↓
 VirtualWorld
      ↓
Task Evaluator
      ↓
 Result + Metrics
```

## 43. 架构原则

### 43.1 Agent 不依赖具体机器人

禁止：

```python
if isinstance(robot, VirtualRobot):
    ...
```

Agent 只能依赖 `RobotBase`。

### 43.2 VirtualRobot 必须可替换

未来 `VirtualRobot` 替换为 `ESP32Robot` 时，不应要求修改 Agent、Prompt、Policy、Validator 和 Task abstraction。

### 43.3 Provider 可替换

未来切换 Gemini Flash、GPT mini、GPT nano、其他 Flash 模型或本地模型时，Agent 核心逻辑不应修改。

### 43.4 Policy 与 Skill 明确分离

V0：

```text
Policy = 针对当前任务动态生成的一段代码
```

未来：

```text
Skill = 已验证、可以长期复用的通用能力
```

V0 不实现 Skill。

### 43.5 Execution Success 与 Task Success 分离

必须保留：

```text
execution_success
task_success
```

不能合并。

### 43.6 实时控制不属于 LLM

V0 虽然只有虚拟机器人，但架构必须遵守：

```text
LLM 负责生成 Policy
Policy 负责组合高层 API
底层机器人负责具体执行
```

未来不得让 LLM 直接控制 PWM、GPIO 或电机 tick。

## 44. V0 验收任务

### Task 1

```text
向前走2米。
```

要求：`execution_success = true`、`task_success = true`、Pose 正确、Metrics 完整。

### Task 2

```text
向前走2米，左转90度，再向前走1米。
```

要求最终 Pose 正确。

### Task 3

```text
一直向前走，每次0.1米，距离障碍物小于0.5米时停止。
```

要求 Policy 使用循环、最终距离满足条件、`task_success = true`。

### Task 4

让模型生成：

```python
robot.stop()
```

但任务要求：

```text
向前走2米
```

要求：

```text
execution_success = true
task_success = false
```

### Task 5

故意生成：

```python
import os
os.system("...")
```

要求 Validator 拒绝，Worker 不启动。

### Task 6

故意生成：

```python
while True:
    pass
```

要求 Worker 被超时终止，主程序继续正常运行，`error_type = PolicyTimeoutError`。

### Task 7

故意生成：

```python
while True:
    robot.move(0.01)
```

要求 `ActionLimitExceeded`。

## 45. V0 Definition of Done

只有以下全部满足，V0 才算完成：

- [ ] Python 项目可安装运行
- [ ] `pytest` 全部通过
- [ ] VirtualWorld 正常
- [ ] VirtualRobot 正常
- [ ] RobotBase 抽象完成
- [ ] LLM Provider 抽象完成
- [ ] MockProvider 可离线运行
- [ ] OpenAI-Compatible Provider 可配置
- [ ] 自然语言可以生成 Python Policy
- [ ] Policy 提取正常
- [ ] AST Validator 正常
- [ ] 未知 API 会被拒绝
- [ ] Policy 在独立子进程执行
- [ ] 死循环能够被真正终止
- [ ] Action Limit 生效
- [ ] Robot API Limits 生效
- [ ] Execution Result 结构化
- [ ] Execution Success 与 Task Success 分离
- [ ] TaskEvaluator 正常
- [ ] Metrics 完整
- [ ] 每次 Run 可以保存 JSON
- [ ] Benchmark 可以批量运行
- [ ] Benchmark 可以输出汇总指标
- [ ] CLI 可以完成一次完整流程
- [ ] README 完整
- [ ] 上层 Agent 不依赖 VirtualRobot 实现
- [ ] 未提前实现 V0 范围外功能

## 46. V0 完成后先做什么

V0 完成后不要立刻加功能。先运行 baseline benchmark。

建议：

```text
20 个固定任务 × 每模型 5 次
```

优先比较：

```text
Task Success Rate
Median LLM Latency
P95 LLM Latency
Total Latency
Input Tokens
Output Tokens
Policy Lines
Action Count
Failure Types
```

根据真实数据再决定下一阶段优先级。

## 47. V0 后续路线

### V0.1 候选方向

根据 V0 数据决定是否加入简单 2D 可视化。

### V0.2 候选方向

可能加入：

```text
Generate
↓
Execute
↓
Feedback
↓
LLM Patch / Replan
↓
Execute
```

但必须在 V0 baseline 完成后再做。

### V0.3 候选方向

Policy Memory / Policy Reuse：

```text
Retrieve
↓
Can Reuse?
├─ 参数替换
├─ Patch
└─ Generate
↓
Execute
```

用于和 V0 的纯 Generate baseline 对比。

### V1

接真实机器人：

```text
Agent
↓
RobotBase
↓
ESP32Robot
↓
Wi-Fi / UART
↓
ESP32
↓
PID / Motor
```

### V2

加入真实感知：

```text
Camera
ToF
IMU
Encoder
```

### V3

加入完整 CaP Agent：

```text
Policy Retrieval
Policy Adaptation
Execution Feedback
Visual Feedback
Skill Library
Skill Synthesis
```

### V4

部署到 Linux SBC，例如 Raspberry Pi、Orange Pi 或其他 SBC，电脑退出运行链路。

## 48. Codex 实施顺序

请严格按以下顺序开发：

```text
1. 数据模型
   Pose / RobotState / Task / Result / Metrics

2. VirtualWorld
   geometry / obstacles / ray distance

3. RobotBase + VirtualRobot

4. Robot / World 单元测试

5. TaskEvaluator

6. AST Validator

7. Worker Process Executor
   timeout / termination / action limits

8. Executor 单元测试

9. MockProvider

10. OpenAICompatibleProvider

11. Agent Coder + Prompt

12. CLI

13. Run JSON / Metrics

14. Benchmark Tasks

15. benchmark.py

16. 集成测试

17. README
```

每完成一个主要模块都运行：

```bash
pytest
```

并保持测试通过。

## 49. Codex 开发原则

优先级：

```text
正确性
>
架构清晰
>
可测试
>
边界明确
>
可测量
>
安全限制
>
代码简洁
>
功能数量
```

禁止：

```text
为了“更智能”提前实现 V1/V2/V3
为了省事引入大型 Agent Framework
为了赶进度跳过测试
为了漂亮架构过度抽象
为了兼容未来写大量当前用不到的代码
```

## 50. Codex 最终交付要求

完成后必须：

1. 运行完整 `pytest`。
2. 运行 MockProvider 集成测试。
3. 运行一次 CLI 示例。
4. 运行一次 Benchmark 示例。
5. 检查无限循环是否可被终止。
6. 检查非法 import 是否被拒绝。
7. 检查 `execution_success != task_success` 的案例。
8. 更新 README。
9. 输出最终项目目录。
10. 简要说明已实现内容、未实现内容、已知限制和测试结果。

不要在 V0 交付中实现：

```text
Policy Reuse
Skill Library
Replan
Vision
ESP32
```

## 51. V0 最终目标

V0 的最终价值不是：

> 做出一个会在二维世界里移动的小机器人。

真正目标是建立一个稳定、可测量的实验基座，用来回答：

> **现代快速 LLM 在固定 Robot API 下，从自然语言现场生成机器人 Policy，到底能有多快、多准、多稳定？**

如果 V0 可以稳定得到：

```text
自然语言
↓
Policy
↓
Validate
↓
Execute
↓
Task Verification
↓
Metrics
```

那么 V0 即成功。

此后所有高级能力：

```text
Policy Reuse
Patch
Replan
Skill Synthesis
Vision
实体机器人
Linux SBC
```

都应建立在这份 baseline 之上，而不是提前混入 V0。
