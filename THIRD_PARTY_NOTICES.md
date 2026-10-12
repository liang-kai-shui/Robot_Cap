# 第三方代码来源与许可

本项目保留第三方源代码随附的许可；本说明不替代完整许可文本。

| 组件 | 来源 | 许可文件 | 本项目情况 |
| --- | --- | --- | --- |
| BMI3XY / BMI323 SensorAPI | [Bosch Sensortec BMI3XY_SensorAPI](https://github.com/boschsensortec/BMI3XY_SensorAPI)，已记录提交 `b3033e78bc6e2c2e473f24e6d79afef0e16c4655` | [Bosch LICENSE](firmware/esp32s3/main/bosch/LICENSE) | 上游来源见 `SOURCE.txt`，本地源文件哈希见 `SHA256.json` |
| VL53L1X 驱动 | [Pololu VL53L1X Arduino library](https://github.com/pololu/vl53l1x-arduino)，原有驱动也基于 ST 的 VL53L1X API | [ST/Pololu LICENSE](firmware/esp32s3/main/tof/LICENSE.txt) | 从现有已编译工程归档；原获取记录未保存确切上游提交，不能声称是某个固定发布版 |
| ESP-IDF | [Espressif ESP-IDF](https://github.com/espressif/esp-idf)，本地使用5.1.2 | 安装的ESP-IDF自身许可 | 构建工具链外部安装，不把整个SDK或编译产物复制到本仓库 |

`main/tof/Arduino.h`、`Wire.h`、`Wire.cpp` 是为了让原驱动通过 ESP-IDF I²C 运行而提供的兼容层，不是完整 Arduino Core。`bmi_init.inc` 和 `tof_port.cpp` 是本项目的适配代码。

高层 Python 项目沿用已有的包依赖声明。仓库此前没有根目录项目许可证，本次整理不擅自新增对所有自有代码的开源授权；第三方文件仍按各自许可处理。
