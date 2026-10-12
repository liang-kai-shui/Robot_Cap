# 文档索引

**最新v1.9/PARAM2扩展**：[能力、低延迟遥控与验证边界](ROVER_V19.md)。旧v1.8记录保留为兼容/历史说明；v1.9新增动作需要烧录新固件。

## 第一次阅读

1. [项目首页](../README.md)：项目目标、当前能力和目录。
2. [项目状态](PROJECT_STATUS.md)：已实测、仅软件验证及尚未实现的区别。
3. [硬件接口讲解](HARDWARE_INTERFACES_GUIDE.md)：逐个解释排针、电源、GPIO、PWM、I²C及编码器。
4. [系统架构](ARCHITECTURE.md)：模型、Worker、可信Runtime、串口和固件怎样协作。
5. [CAP 能力接入核对](CAP_INTEGRATION_AUDIT.md)：高层功能逐项实车对应、已验证 Policy 复用、Episode 与性能指标。

## 运行、构建和复现

| 文档 | 内容 |
| --- | --- |
| [WebUI 使用说明](WEBUI.md) | 双击启动、设备回传、按钮控制、自然语言、终端及构建 |
| [手动 PWM](MANUAL_PWM.md) | 实际0–100%占空比、pwm-stream-v2、续租和闭环检查边界 |
| [姿态与测距校正](POSE_RANGE_CALIBRATION.md) | 排针视角、偏移、航向有效性与恢复 |
| [WebUI 设计方案](WEBUI_DESIGN.md) | 界面布局、视觉规范、设计参考和验证范围 |
| [实车使用说明](ESP32_HARDWARE.md) | 环境、自然语言/连续/分段控制、上下文、停止和计时 |
| [完整固件说明](../firmware/esp32s3/README.md) | ESP-IDF配置、编译、主动烧录、源文件职责 |
| [串口协议](SERIAL_PROTOCOL.md) | PARAM1/PARAM2格式、能力扩展、目标匹配、结果和停稳确认 |
| [复现说明](REPRODUCING.md) | Python测试、固件C检查、完整编译与历史版本 |
| [模拟实验详细指南](SIMULATION_GUIDE.md) | 原有Benchmark、P1/P2导航、P3规划、P4扰动、指标 |
| [原始V0需求](V0_REQUIREMENTS.md) | 最初的软件原型要求；历史文档，不代表当前只支持模拟 |

## 实验报告

最新研究：[相似项目调研与优化路线](../reports/related_projects_and_roadmap_2026-10-13.md)。比较CaP、技能/具身框架、ESP32底盘与网页遥控，区分已有能力、建议改进和暂缓迁移。

[报告索引](../reports/README.md)集中说明各批次日期、协议和数据文件。实车记录和模拟成绩分开存放和解释。

原始运行记录、开发机路径和API密钥不作为复现资料提交；共享报告提供必要设置与结果摘要。

维护目录、版本和实车记录见 [贡献与记录规范](../CONTRIBUTING.md)，近期用户可见变化见 [变更记录](../CHANGELOG.md)。
