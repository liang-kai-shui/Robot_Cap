# ESP32 v1.8 / PARAM1串口协议

电脑与开发板使用115200 baud、UART0控制台。一次只有一个串口客户端；不与键盘工具或IDF monitor同时连接。

## 连接确认

电脑在开串口前设置DTR/RTS=False，打开后清接收缓冲并发送T。必须收到本次STOP确认以及兼容模式的新READY。要求字段：`firmware=1.8 protocol=PARAM1 front_guard=OFF`，旧固件不自动降级使用。

## 指令

| 字节/帧 | 用途 |
| --- | --- |
| `T` / `t` | 停止，优先级高于其他输入 |
| `@F100\n` | 前进100mm；允许50–300mm整数 |
| `@J300\n` | 左转30.0°；允许150–900个0.1°单位 |
| `@L300\n` | 右转30.0°；同样的正数参数表示右转幅度 |
| `f` / `j` / `l` | 保留旧固定300mm / 左90° / 右90°指令 |

Python API以米/度为参数，后端转换为整数帧；不会静默四舍五入范围外或不满足步长的输入。完整换行帧通过校验后才启动；支持分段接收，帧超时250ms、非法数值和混合动作拒绝。运动期间不排队新的动作。

## 运动确认顺序

```text
发送一次指令
  → VEHICLE_START：动作与目标匹配
  → VEHICLE_STOP：本次目标触发关闭驱动
  → DISTANCE_RESULT 或 TURN_RESULT：测量有效、目标与结果一致
  → VEHICLE_DONE：本次动作完成
  → VEHICLE_READY：新的停稳确认
```

缺任何一步都不能算动作成功。BOOT/T中止、目标不匹配、传感器异常、超时、复位或断线使会话禁止后续运动。没有自动重发动作或重连续跑。

距离结果包含关闭时/关闭后左右轮编码器估计、均值、方向误差和固件耗时。转角结果包含本次相对目标、关闭角度、关闭后角度和误差。数据没有绝对坐标或真实地面位置含义。

## STOP确认

T可以发送时不等待运动动作锁；正常STOP调用等待驱动关闭ACK及后续停稳READY。STOP写成功不等于轮子已经物理停稳。紧急停止会锁定当前会话，不能通过预算更新清除。

## 关联与去重

PC Runtime的command ID在IPC中使用，重复ID返回缓存结果。固件串口没有command ID，因此串口只允许单动作顺序执行和目标匹配；不因等待超时重发运动。重启后新任务没有跨进程去重保证。

## 停止与计时上限

固件前进驱动最长6秒，转弯最长3秒；电脑move/turn handler的9/6秒期限包含反馈和停稳等待。任务模式另有180秒PC计时线程，覆盖模型和动作；所有这些期限职责不同，不能相加作为某个动作的允许时长。

详细结果字段、中文计时与运行日志见[实车说明](ESP32_HARDWARE.md)。

## 手动短时后退

WebUI的后退按钮发送已有单字节`s`，固件按原定800ms短时模式运行。它没有按距离控制，也没有DISTANCE_TARGET/ANGLE_TARGET停止日志。电脑专门核对START的`action=s`、左右`sign=-1`和`duration=800ms`，再核对A/RIGHT、B/LEFT两个VEHICLE_RESULT的编码方向、计数与边沿一致性，接收定时VEHICLE_DONE（800–900ms）后仍等待新的READY。缺少结果、错误方向、意外STOP或超时都停止并中止会话，不重发。此路径只用于可信手动控制，不扩展Policy的move参数范围。

## WebUI遥测扩展（2026-10-12）

协议仍为1.8/PARAM1，READY新增`tof=ON`。运动确认的顺序不变。下列只读日志约每250ms产生，空闲或运动时均可回传；PC不把它们当作动作完成确认。

```text
TOF ready=1 distance_mm=423 valid=1 status=0 error=0 age_ms=12
WHEEL motor=A side=RIGHT count=-1234 forward_count=1234 cps=1919.0 speed_mm_s=100.0 rpm=44.42 valid=1 dt_ms=250.0 age_ms=0
ATTITUDE roll=2.0 pitch=-3.0 yaw=10.0 tilt_valid=1 yaw_valid=1 calibrated=1 age_ms=4 zero_epoch=0
```

WHEEL正值表示轮子的车辆前向，不是排针电机极性；左右轮分别用19.190/19.294 counts/mm，RPM按43mm轮径计算。TOF有效性同时受光学status、总线错误和采样年龄约束。ATTITUDE横滚/俯仰是传感器轴下估计，相对yaw无绝对航向含义。WebUI叠加电脑接收后的年龄，过期或无效时冻结动画并隐藏当前测量值。测距显示不改变front_guard=OFF。
