# 完整 ESP32-S3 小车固件

这是可独立构建的 ESP-IDF 工程，源自当前小车使用的 v1.8/PARAM1 固件。包含双路电机、编码器、BMI323、VL53L1X 驱动和所有控制头文件；不依赖仓库外的 `0_HelloWorld` 工程。

## 环境与硬件配置

- 已使用的工具链：ESP-IDF **5.1.2**，目标 `esp32s3`。
- 默认硬件配置：8MB Flash、8MB Octal PSRAM、CPU 240MHz、FreeRTOS tick 1000Hz。
- 控制台：UART0，115200 baud。电脑通过已验证的 USB 转串口路径控制。
- `sdkconfig.defaults` 保存关键配置；构建生成的 `sdkconfig` 和 `build/` 不提交。
- 本硬件配置针对当前 N8R8 开发板。其他 Flash/PSRAM 型号需要调整配置，不应直接套用。

在已加载 ESP-IDF 的终端，从仓库根目录运行：

```powershell
idf.py -C firmware/esp32s3 build
```

需要主动烧录时，先退出占用串口的控制程序和 monitor，断开电机 5V、保留 USB，再运行：

```powershell
idf.py -C firmware/esp32s3 -p COM5 flash
```

`build` 只编译；`flash` 会更新板上程序。已有 v1.8 小车使用本次新增的电脑端功能不必重新烧录。

2026-10-12 已在新归档目录从默认配置完成一次完整构建：`robot_rover.bin` 为 `0x484c0` 字节，1MB应用分区剩余72%。未连接串口或烧录。启动日志的Project name/App version可能随工程名和Git版本改变；电脑端核对的是READY中的v1.8/PARAM1协议标记。

## 文件职责

| 文件/目录 | 职责 |
| --- | --- |
| `main/main.c` | UART 命令、GPIO/PWM/PCNT、运动状态机、停止与故障处理 |
| `main/command_parser.h` | PARAM1 参数帧解析、分段接收、超时和停止优先 |
| `main/speed_pi.h` | 按编码器反馈调节左右电机驱动 |
| `main/distance_control.h` | 距离换算、轮子标定系数和检查 |
| `main/heading_control.h`、`main/turn_control.h` | 方向保持与相对角度转弯 |
| `main/sensor_io.c`、`main/bmi_init.inc` | 传感器采样、初始化、校准与状态 |
| `main/motion_math.h`、`main/imu_sample.h` | IMU 数据处理与角度有效性检查 |
| `main/range_guard.h`、`main/test_mode.h` | 前方测距保护逻辑及当前人工监看配置 |
| `main/tof_port.cpp`、`main/tof/` | 测距驱动与 ESP-IDF I²C 兼容层 |
| `main/bosch/` | BMI323 的上游驱动、来源说明及许可 |
| `keyboard_control.py` | 原 Windows 键盘调试工具；与 CaP 程序互斥占用串口 |

## 接线和协议

A=右轮：编码器10/11、方向12/13、PWM14；B=左轮：编码器4/5、方向16/17、PWM18；共同 STBY15。BMI323 SDA8/SCL9，VL53L1X SDA6/SCL7。电源及线色见 [硬件接口讲解](../../docs/HARDWARE_INTERFACES_GUIDE.md)。

PARAM1：`@F100\n` 前进100mm，`@J300\n` 左转30.0°，`@L300\n` 右转30.0°；T/t优先停止。单次前进50–300mm，转弯15–90°。动作反馈顺序和电脑端调用见 [串口协议说明](../../docs/SERIAL_PROTOCOL.md)。

`main/test_mode.h` 当前为 `ROBOT_SUPERVISED_TEST=1`：ToF采集和前方停车保护关闭；编码器和BMI反馈、运动时间上限及T/BOOT停止仍在。不要仅为启用新电脑端模式改动这个开关。

## 原键盘工具

在 Windows、装有 pyserial 的 Python 环境中：

```powershell
python firmware/esp32s3/keyboard_control.py --port COM5
```

WASD是短时运动，F/J/L是固定30cm/90°动作，T停止，Q退出，C/E是断开电机5V后的手推标定工具。日常自然语言控制使用仓库根目录的 `main.py --backend esp32`；不要同时运行两个串口控制程序。

## 第三方代码

保留 Bosch 和 ST/Pololu 的 BSD-3-Clause 许可及原始版权声明。来源和本项目兼容层的区分见 [第三方代码说明](../../THIRD_PARTY_NOTICES.md)。
