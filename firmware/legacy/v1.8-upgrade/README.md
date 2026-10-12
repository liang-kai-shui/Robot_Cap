# ESP32-S3 v1.8 参数控制

此目录保留既有 ESP-IDF 工程的主循环和修改后的控制头文件，依赖原工程的传感器驱动、标定及其他头文件；不是独立 ESP-IDF 工程。新克隆仓库请使用[完整工程](../../esp32s3/README.md)，本目录只保留旧迁移方式。

新增 PARAM1 帧使前进目标可设为50–300mm、左右转目标可设为15–90°。接线、速度、最长运动时间、编码器与BMI保护沿用已实测版本，测距采样和前方停车保护保持关闭。参数步长1mm/0.1°不代表实际动作精度。

安装脚本只替换 `main.c`、`turn_control.h` 并新增 `command_parser.h`；安装前核对原 v1.7 文件 SHA256，拒绝覆盖未知版本，先保存带时间戳的备份。不会打开串口、烧录或运动：

```powershell
& 'D:\Espressif\python_env\idf5.1_py3.11_env\Scripts\python.exe' 'D:\Robot_Cap\firmware\legacy\v1.8-upgrade\install_firmware.py' --project 'D:\YourExistingIdfProject'
```

`distance_control.h` 是沿用的标定/限值副本，不需要替换原文件。其他原工程文件也保留。

在 ESP-IDF 终端编译与烧录（烧录前退出旧串口程序并拔电机5V，保留USB）：

```powershell
Set-Location 'D:\YourExistingIdfProject'
idf.py build
idf.py -p COM5 flash
```

电脑端要求 `firmware=1.8 protocol=PARAM1 front_guard=OFF`，收到停稳READY后才开始。固定演示和连续控制命令见 `D:\Robot_Cap\docs\ESP32_HARDWARE.md`。电脑端计时无需额外接线，保存在各动作结果和任务日志的 `timings_ms`。

帧解析与变角度控制的离线C单元检查：

```powershell
& 'C:\MinGW64\mingw64\bin\gcc.exe' -std=c11 -O0 -Wall -Wextra -I 'D:\Robot_Cap\firmware\legacy\v1.8-upgrade' 'D:\Robot_Cap\tests\firmware_param_check.c' -o 'D:\Robot_Cap\.tmp\firmware_param_check.exe'
& 'D:\Robot_Cap\.tmp\firmware_param_check.exe'
```

此检查不连接实车。完整主循环另经过离线模拟检查，覆盖参数帧分段、停止优先、非法/超时拒绝、运动时不排队及既有故障保护；部分新目标和两段任务已有用户实车反馈，地面精度仍需独立测量。
