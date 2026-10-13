# 构建、测试与结果复现

## Python高层项目

要求Python3.11+。从仓库根目录运行：

```bash
python -m venv .venv
# 激活环境后
python -m pip install -e ".[dev,hardware]"
python -m pytest -q -W error
python scripts/check_repository.py
```

测试使用mock模型、模拟后端和假串口；不会调用付费模型、连接COM5、烧录开发板或让轮子转动。2026-10-13 本次完整离线回归通过545项测试（严格警告模式），包括已验证策略复用、航向恢复、CLI/WebUI共用Episode、模型审核用量、PWM恢复分类和摇杆协议；文件归档与文档检查另由检查脚本完成。模拟任务与评分协议未因实车策略库而改变。

默认模拟入口：

```bash
python main.py --backend virtual --task-id B1 --auto
python benchmark.py --provider mock
```

真实模型需要自行设置API环境变量；凭证不上传。各模拟协议、种子、任务和指标见[模拟指南](SIMULATION_GUIDE.md)与[报告索引](../reports/README.md)。

## 固件独立C检查

在带gcc的终端，从仓库根目录运行：

```bash
mkdir -p .tmp
gcc -std=c11 -O0 -Wall -Wextra -Werror -I firmware/esp32s3/main tests/firmware_param_check.c -lm -o .tmp/firmware_param_check
.tmp/firmware_param_check
gcc -std=c11 -O0 -Wall -Wextra -Werror -I firmware/esp32s3/main tests/firmware_encoder_snapshot_check.c -o .tmp/firmware_encoder_snapshot_check
.tmp/firmware_encoder_snapshot_check
gcc -std=c11 -O0 -Wall -Wextra -Werror -I firmware/esp32s3/main tests/firmware_vector_check.c -o .tmp/firmware_vector_check
.tmp/firmware_vector_check
```

Windows可创建`.tmp`后，用已安装的gcc.exe执行相同参数，并给输出文件加`.exe`。这些检查覆盖参数帧、范围、分段/连读接收、停止优先、PWM 续租与闭环目标超速检查、转角控制、编码器采样窗口及全部40,401个有符号左右轮组合，不使用串口，也不替代完整ESP-IDF构建。

## 完整固件构建

安装ESP-IDF5.1.2并加载其环境，执行：

```bash
idf.py -C firmware/esp32s3 build
```

或者使用Espressif官方Docker镜像，在支持Docker的环境中执行：

```bash
docker run --rm -v "$PWD:/project" -w /project espressif/idf:v5.1.2 idf.py -C firmware/esp32s3 build
```

官方镜像用途见[Espressif Docker说明](https://github.com/espressif/esp-idf/blob/v5.1.2/docs/en/api-guides/tools/idf-docker-image.rst)。完整固件的关键默认配置见[sdkconfig.defaults](../firmware/esp32s3/sdkconfig.defaults)，实际构建信息见[固件说明](../firmware/esp32s3/README.md)。本节构建命令不烧录；烧录需按固件说明由用户明确操作。

2026-10-13 最新本地结果：Python 545项通过（严格警告模式），三组固件C检查PASS；`pwm-stream-v6`完整工程已构建并烧录，应用大小`0x4fd80`、1MB分区剩余69%。烧录后确认READY、双轮静止和传感器回传，不作为运动验证。本次GitHub整理不再次烧录；网页控制方式只需刷新，默认四轴、摇杆可选。固件目录保留字节级归档属性，避免Windows/Linux换行转换破坏源码哈希。

## GitHub自动检查

[CI配置](../.github/workflows/ci.yml)运行Python3.11/3.12离线测试、仓库文档和源文件检查、固件C检查及ESP-IDF5.1.2完整构建。CI没有机器人连接或模型凭证，不能作为实车实验成绩。Python动作配置参考[GitHub官方Python测试文档](https://docs.github.com/en/actions/tutorials/build-and-test-code/python)。

## 实车复现与证据

用户按[实车说明](ESP32_HARDWARE.md)主动运行控制命令。记录分别保存模型请求、完整计划、每段策略、反馈审查、Runtime trace、原始串口及各环节耗时。操作结束后才汇总本次记录。

公开的[实车报告](../reports/physical_integration_2026-10-12.md)来自用户提供的终端记录。发布的是必要测量与设置，不包含本地完整日志、API密钥或个人目录。单次通过只证明本次接口流程，不证明统计可靠性或真实定位精度。

## 历史版本

当前`main`展示完整项目；`v1-runtime-hardening`保留维护线。原有历史标签保留：

| 标签 | 阶段 |
| --- | --- |
| `v0.1-baseline` | 原始模拟CaP |
| `v0.2-baseline` | Policy契约收紧 |
| `v1-runtime-foundation-baseline` | 可信Runtime和Worker基础 |
| `v1-runtime-hardening-baseline` | Runtime加固与交互动作约束 |

本次快照标签`physical-cap-2026-10-12`包含完整实车接入与固件归档。复现旧版使用`git switch --detach <标签>`，不需要改写当前分支历史。报告中引用的旧提交仍属于完整提交历史。

## 提交边界

上传源代码、关键配置、许可、技术说明及整理过的报告/汇总。运行记录在`runs/`本地保存；`.env`、虚拟环境、编译目录、sdkconfig、日志、备份和临时检查目录不提交。不要把生成的固件二进制作为不经构建核对的通用烧录包。

2026-10-13 `pwm-stream-v3` 编译、烧录和静止回传验证通过；应用 `0x4e950`，1MB 分区剩余69%。只发送 T 的两次实机重新连接成功，握手分别约107ms和109ms；未驱动电机。低 PWM 停止和通道恢复由离线测试及模拟网页验证，详见 [恢复说明](PWM_RECOVERY.md)。
