# Robot CaP：模拟研究与 ESP32-S3 实车

当前实车固件为 **v1.9 / PARAM2 / pwm-stream-v6**。WebUI 默认“四轴操控”（前进、后退、左转、右转），可切换为 [连续差速摇杆](docs/JOYSTICK_CONTROL.md)；两者共用 [0–100% PWM 滑块](docs/MANUAL_PWM.md)，默认 40%，0 停止。切换结束旧手势，刷新恢复四轴。

v6 支持连续弧线调速、停稳反转及更短的手动等待。它保留 v4 的 [编码器采样与故障帧诊断](docs/ENCODER_SNAPSHOT.md)、v5 的 [手动可恢复停止分类](docs/PWM_RECOVERY.md)，闭环和严重异常仍锁定。用户曾反馈 v4 在 40% 架空及平地正常、不平整桌面触发保护；v6 静止回传与离线逻辑已验证，运动可靠性仍需实车记录。

自然语言任务通过模型生成 Python Policy，经能力注册、AST 校验和独立 Worker，由可信 RobotRuntime 执行。项目包含二维模拟评测与 ESP32-S3 实车后端，分别记录执行反馈和任务结果。

**当前状态（2026-10-13）：** 双电机、编码器、BMI323、有界参数动作、DeepSeek 自然语言控制和会话上下文已有用户实车记录；统一入口的两段反馈任务也有一次用户验证。WebUI 提供测距、轮速/车速、3D 姿态、手动控制、模型诊断和策略复用。ToF 采样 ON，普通前方停车保护 OFF；未接入实车定位、自主导航或避障。完整 ESP-IDF 工程随仓库提供。各新增动作的验证范围分别见 [项目状态](docs/PROJECT_STATUS.md)，软件测试和静止回传不等于实车运动验证。

[文档索引](docs/README.md) · [硬件接口讲解](docs/HARDWARE_INTERFACES_GUIDE.md) · [系统架构](docs/ARCHITECTURE.md) · [项目状态](docs/PROJECT_STATUS.md) · [报告索引](reports/README.md) · [变更记录](CHANGELOG.md) · [贡献与记录规范](CONTRIBUTING.md)

## 阅读与使用入口

**研究与后续方向：** [相似项目调研与优化路线](reports/related_projects_and_roadmap_2026-10-13.md)，比较15个项目或组件，讨论近期稳定性、延迟、技能复用与未来定位/ROS选择；近期落地见 [第一轮优化](docs/ROADMAP_OPTIMIZATION.md)，其余扩展仍是研究建议。

| 目的 | 从这里开始 |
| --- | --- |
| 用网页连接小车 | [WebUI 使用说明](docs/WEBUI.md) |
| 了解硬件与接线 | [硬件接口讲解](docs/HARDWARE_INTERFACES_GUIDE.md) |
| 了解模型怎样调用真实动作 | [系统架构](docs/ARCHITECTURE.md)、[CAP 接入核对](docs/CAP_INTEGRATION_AUDIT.md) |
| 复现模拟或离线测试 | [模拟指南](docs/SIMULATION_GUIDE.md)、[复现说明](docs/REPRODUCING.md) |
| 构建 ESP32 固件 | [固件入口](firmware/README.md)、[完整工程](firmware/esp32s3/README.md) |
| 看实验结论与证据边界 | [报告索引](reports/README.md)、[当前状态](docs/PROJECT_STATUS.md) |

**CAP 接入核对与策略复用：** 核心 Registry、Worker、Runtime、反馈、上下文和指标已接入实车。新增 CLI/WebUI 共享的已验证策略库：明确任务完成后学习，重复任务跳过模型生成，仍重新校验、执行并等待新反馈。预览、失败、上下文引用和动态测距任务不直接复用；地图导航等仍需真实定位能力。详见 [CAP 接入核对](docs/CAP_INTEGRATION_AUDIT.md)。

## 目录

| 路径 | 内容 |
| --- | --- |
| `main.py` | 统一入口，默认模拟，显式选择实车 |
| `physical_control.py` | 实车状态、固定演示、自然语言和连续/分段控制 |
| `web_control.py`、`webui/` | 本机网页控制台：回传、按钮、自然语言、终端和构建 |
| `agent/`、`providers/` | 策略生成、上下文、任务规划和模型服务 |
| `policy/` | 共享 Episode 与实车已验证策略库 |
| `robot/`、`runtime/` | 能力注册、可信调度、Worker、后端和停止 |
| `navigation/`、`world/`、`task/` | 模拟地图导航、环境和任务评分 |
| `metrics/`、`config/` | 指标记录与模拟配置 |
| `firmware/esp32s3/` | **完整可构建 ESP-IDF 工程**，含传感器驱动和配置 |
| `firmware/legacy/` | 早期已知版本固件升级包，保留迁移记录 |
| `docs/` | 入门、架构、运行、协议、复现及历史需求 |
| `reports/` | 分类实验报告、整理后的 JSON/CSV |
| `scripts/`、`tests/` | 复测/检查脚本及离线测试 |
| `runs/` | 本地运行记录，默认不上传 |

## 两种后端

| 能力 | 模拟 | 实车 |
| --- | --- | --- |
| 移动/转弯 | 模拟 API 范围与可选速度 | v1.9前进/后退5–30cm、左右转15–90°；另有测距接近和人工按住遥控 |
| 位姿与测距 | 按实验使用理想或扰动观测 | 无地面位姿；v1.9公开有效测距和传感器姿态观测 |
| 任务执行 | Benchmark、观测导航和低频规划 | 有界动作、上下文和分段反馈审核 |
| 评分 | 独立模拟真值与TaskEvaluator | execution_success；无独立地面评分，task_success=null |

模拟成绩不能当作实车成绩。实车 Registry 只开放当前实现的接口，不提供虚拟坐标或地图。

## 安装与离线运行

要求 Python 3.11+，从仓库根目录运行：

```bash
python -m venv .venv
# 激活环境后
python -m pip install -e ".[dev,hardware]"
python main.py --backend virtual --task-id B1 --auto
python -m pytest -q
python scripts/check_repository.py
```

默认 `mock` 不需要模型密钥。自然语言真实模型与各批模拟实验命令见 [模拟指南](docs/SIMULATION_GUIDE.md)。配置模板见 [.env.example](.env.example)，项目不自动读取 .env；通过终端设置环境变量，不把密钥上传到仓库。

## 实车使用

**网页入口：** Windows 双击 [Start_Robot_UI.vbs](Start_Robot_UI.vbs)，在浏览器选择串口并连接，无需反复打开 PowerShell。控制台提供姿态回传、动作反馈、计时、手动控制、自然语言计划、任务记录和机器人命令终端。旧v1.8/PARAM1兼容原步进控制；持续控制、按距离后退与测距接近需v1.9/PARAM2固件。离线体验可双击 [Preview_Robot_UI.vbs](Preview_Robot_UI.vbs)，详细操作见 [WebUI 使用说明](docs/WEBUI.md)。WebUI 已验证实车静止遥测及浏览器显示，新增遥测固件的运动验证待用户执行。

硬件：ESP32-S3 N8R8、TB6612、双N20霍尔编码电机、43mm轮胎、BMI323、已启用采样的VL53L1X。具体引脚、电源和非通用线色见 [硬件接口讲解](docs/HARDWARE_INTERFACES_GUIDE.md)。

```bash
# 只连接、停止并读状态，没有运动
python main.py --backend esp32 --port COM5
# 连续自然语言任务，每条最多3运动+STOP
python main.py --backend esp32 --port COM5 --interactive --execute
# 分段任务：完整计划最多9运动，每段最多3；停稳后审核反馈
python main.py --backend esp32 --port COM5 --interactive --mission --execute
```

省略 `--execute` 的自然语言/连续模式只预览，不打开串口。`--demo` 是明确执行固定演示的独立选项。实车入口不接受模拟模式的 `--auto`。

当前默认服务 DeepSeek `deepseek-flash`。密钥从 `LLM_API_KEY` / `DEEPSEEK_API_KEY` 读取，没有时在本机隐藏输入一次，不写入记录。COM5 是用户已验证的端口，新电脑应选择实际端口。

测试任务示例：`前进10厘米，左转30度，右转30度，再前进10厘米，最后停止`。整项计划先完整校验；段间模型只判断继续或中止，不能增加、改动或重试动作。总运动预算跨段累计，任务限时180秒。T/Q/Ctrl+C/BOOT 可中止；故障或停止后不自动重连续跑。真实使用需关闭旧串口程序，车静止校准，按当前人工监看设置操作。

运行、计时和上下文说明见 [实车指南](docs/ESP32_HARDWARE.md)，协议见 [串口说明](docs/SERIAL_PROTOCOL.md)，最近一次结果见 [实车记录](reports/physical_integration_2026-10-12.md)。

## 固件构建

已加载 **ESP-IDF 5.1.2** 环境时，从仓库根目录运行：

```bash
idf.py -C firmware/esp32s3 build
```

完整工程包含主循环、PWM/编码器控制、BMI323 与 VL53L1X 驱动、校准和 PARAM1/PARAM2 解析。关键配置写在 sdkconfig.defaults，构建目录不上传。主动烧录命令、源文件职责和硬件型号限制见 [固件 README](firmware/esp32s3/README.md)。已有v1.8设备使用原步进功能不必烧录；v1.9新增动作需要更新固件。网页设置提供编译并烧录入口，操作前拔电机独立5V、仅保留USB并断开网页串口。

## 验证与研究记录

- 当前 Python 离线回归：**545项通过**（含 WebUI、策略复用、航向恢复与共享指标），使用假串口和mock模型，不连接实车或付费API。
- 固件归档逐文件核对来源；另有参数帧/转角 C 检查和完整 ESP-IDF 构建。
- GitHub CI 运行离线检查和固件构建，验证构建能力，不证明实车精度。
- 2026-10-12 用户两段任务执行完成并停稳，耗时约22.7秒；反馈测量不是独立地面定位。
- 历史模拟报告保留各自任务协议、预算、种子、成功判据和模型配置，见 [报告索引](reports/README.md)。

详细复现步骤见 [REPRODUCING](docs/REPRODUCING.md)。

## 分支、历史与许可

`main` 展示当前完整项目，`v1-runtime-hardening` 保留维护线。原 V0.1/V0.2/Runtime 标签继续保留；本次快照为 `physical-cap-2026-10-12`。按标签复现，无需重写历史。

第三方驱动的许可、来源和上游版本记录见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md)。本次整理没有擅自为全部自有代码新增开源许可证。

当前姿态显示和尺测偏移更新见 [姿态与测距校正说明](docs/POSE_RANGE_CALIBRATION.md)。5V自动检测已按用户要求取消，原有固件与接线保留。
