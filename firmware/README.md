# ESP32-S3 固件目录

| 目录 | 用途 |
| --- | --- |
| [esp32s3/](esp32s3/README.md) | 当前完整 ESP-IDF 工程；主控、传感器驱动、标定、参数帧及停止逻辑均包含在内 |
| [legacy/v1.8-upgrade/](legacy/v1.8-upgrade/README.md) | 早期从已知 v1.7 工程升级到 v1.8 的补丁及安装脚本，保留作为迁移记录 |

新克隆仓库使用 `esp32s3/`。legacy 是升级包，依赖另一个已经完整配置的旧工程，不能独立编译。

本次归档复制已有固件源文件，没有改动已验证的电机控制、标定或 GPIO，也没有给正在使用的开发板烧录。仓库中的工程名为 `robot_rover`，旧本地工程名为 `0_HelloWorld`；串口协议仍为 `firmware=1.8 protocol=PARAM1 front_guard=OFF`。
