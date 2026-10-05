# 观测地图导航基础：P1/P2 实现与评测

本次完成导航方案的前两步：统一测距观测语义，以及基于实际观测的地图、A* 与边界探索。导航目标由调用者显式给出，导航决策暂不调用模型。尚未实现低频 LLM 子目标规划、视觉、动态障碍、噪声或 Policy Reuse。

## 1. 仓库整理

GitHub 从四条分支整理为两条：`main` 保存 V0.1，`v1-runtime-hardening` 持续开发。本次没有合并到 `main`，没有重写提交历史。

`v0.2-hardening` 和 `v1-runtime-foundation` 都是当前开发线的祖先；确认没有开放 PR 后，先推送对应历史标签，再删除两条远端分支：

| 标签 | 提交 |
|---|---|
| `v0.1-baseline` | `1dc3263` |
| `v0.2-baseline` | `60f3690` |
| `v1-runtime-foundation-baseline` | `b86749f` |
| `v1-runtime-hardening-baseline` | `1dfe664` |

已有 Foundation 评测 worktree 保留，因此本地仍可能看到它使用的旧分支。历史实验报告保留，原始运行数据保留在本地 `runs/`；Git 忽略原始运行目录，提交经过检查的报告与汇总。

## 2. 参考与实现

参考 [PythonRobotics 栅格路径搜索](https://atsushisakai.github.io/PythonRobotics/modules/5_path_planning/grid_base_search/grid_base_search.html) 的栅格 A* 思路，以及 [测距建图教程](https://atsushisakai.github.io/PythonRobotics/modules/3_mapping/lidar_to_grid_map_tutorial/lidar_to_grid_map_tutorial.html) 的射线更新思路。参考 [Nav2 的导航服务器职责划分](https://docs.nav2.org/rolling/getting_started/navigation_concepts/navigation_servers/) 将规划、执行和安全分开；没有引入 ROS 或复制完整上游系统。代码使用标准库和现有项目组件实现。

[Inner Monologue](https://innermonologue.github.io/) 和 [LLM-Planner](https://dki-lab.github.io/LLM-Planner/) 提供闭环反馈与高层规划的后续参考。本次先验证确定性导航层，尚未接入这类模型规划器。

### P1：测距观测契约

`RangeObservation` 包含采样姿态、米制距离、量程、`hit`、`valid`、时间戳、能力身份和空间语义。当前适配器保持 `get_distance()` 返回浮点数，保留旧的 `front_distance_m` 字段，并增加结构化 `range_observation`。

- 超过 2 米量程的读数截断为 2 米、`hit=false`，不会把量程端点伪造为障碍。
- 恰好 2 米的真实命中保留 `hit=true`。
- 无效、非有限、负数或过期观测拒绝用于导航；观测姿态必须与当前姿态一致。
- 当前距离已经包含 VirtualWorld 的 clearance，地图不重复膨胀障碍。原始物理传感器读数必须先由明确的车体适配器转换，不能直接套用。

当前时间戳是同步模拟读数的接收时间，不能证明未来物理传感器的采集新鲜度。当前姿态也是模拟器的精确估计，尚未包含定位误差。

### P2：每个 episode 独立的观测地图与导航

`ObservedGrid` 是稀疏栅格，只更新观测射线涉及的自由区域、命中端点和已扫描方向，其他区域保持 unknown；每次运行重新创建。`LocalNavigator` 只接收公开目标和观测，不接收 World、真实障碍列表或成功判据。

A* 仅走已观测自由格，采用四邻域，避免斜穿未知区域或障碍角落。目标尚不连通时，在已知可达区域选择边界观察位置，结合目标距离、路径长度和重复访问次数评分。保留选中的观察位置直到完成观察，避免到达附近后不断重新评分产生来回振荡。

每次只生成一次 `move`、`turn` 或必要的 `stop` 调用；转向后重新观察，直线移动受最大步长和当前测距安全余量限制。执行链保持：

```text
公开目标 + 局部观测 → 观测地图 / A* / 边界探索
                         ↓
                 单动作 Python Policy
                         ↓
Validator → Worker → RobotProxy → RobotRuntime → motion guard → Backend
```

导航适配器通过 Registry 的 canonical ID 找到公开路径，包括多级别名；没有新增公开导航能力，没有修改通用 Registry、Proxy 或 Runtime 核心。应用层依赖现有运动与测距能力是明确的导航需求，不是对所有未来部件的预设。

异常、预算耗尽或无更多可观察边界会紧急停止。安全拒绝后重新采样和规划，不重放被拒绝的请求。到达公开目标与满足私有任务成功条件分别记录；评估器只在执行后判断后者。

## 3. 评测设置

默认栅格 0.25 米、安全余量 0.15 米、最大单步 1.5 米、动作预算 80、观测预算 160。保留更严格的 Runtime 动作限额。

固定场景包括原有 H1–H4、不同起点和目标、非栅格目标、长墙、交错墙、死胡同、走廊和不可达目标。另使用固定种子 `20261006` 生成 20 个独立静态场景：每场 2–6 个矩形障碍，改变起点、朝向和目标。生成器通过私有地图检查起终点合法及栅格可达性，导航器不接收此信息。没有根据这 20 个场景的结果调参。

CLI 评测使用真实 spawn Worker；部分算法单元测试使用轻量执行器，另有多级公开路径的真实 Worker 集成测试。

这是确定性导航实验，不是 DeepSeek 评测。H1–H4 共用原场景，但本实验动作预算为 80，既有交互评测预算与输入方式不同，不能把成功率或耗时直接当作模型方案胜负。

## 4. 实际结果

### 回归与打包

| 检查 | 结果 |
|---|---|
| `python -m pytest -q --basetemp .pytest-tmp-nav-p1p2-final` | **152 passed in 11.99s** |
| `python benchmark.py --provider mock` | Capability **18/18**；Safety **2/2** |
| `python complex_benchmark.py --provider reference` | Strict success **12/12** |
| `python interactive_benchmark.py --provider reference` | **4/4** |
| 离线 wheel 构建及内容检查 | 成功；包含导航模块和 CLI 入口 |

原始 `task/benchmark_tasks.py` 未修改。Safety 场景的 UnsafePolicyError 和 PolicyTimeoutError 是预期结果。本次未运行新一轮真实模型评测，没有产生 API 费用。

wheel 首次构建因受限环境默认临时目录不可写失败；将 TEMP/TMP 指向工作区临时目录后构建成功，不涉及代码故障。

### 固定场景

| 场景 | 类型 | 到达并完成任务 | 动作数 |
|---|---|---|---|
| H1 | 原交互场景 | 是 | 2 |
| H2 | 原交互场景 | 是 | 16 |
| H3 | 原交互场景 | 是 | 14 |
| H4 | 原交互场景 | 是 | 14 |
| G1 | 不同目标 | 是 | 11 |
| G2 | 不同起点/朝向 | 是 | 10 |
| G3 | 非栅格目标 | 是 | 4 |
| W1 | 长墙绕行 | 是 | 30 |
| W2 | 交错墙 | 是 | 49 |
| U1 | 死胡同 | 是 | 78 |
| C1 | 走廊 | 是 | 4 |
| X1 | 不可达目标 | 否；预算耗尽后停稳 | 80 |

11 个可达场景 **11/11** 完成；1 个不可达场景安全结束。全部 12 次运行无碰撞、无 guard 拒绝，模型调用 0 次。

### 固定种子随机场景与耗时

| 指标 | 固定场景（包含 X1） | 20 个随机可达场景 |
|---|---|---|
| 可达任务完成 | 11/11 | 20/20 |
| 碰撞 / guard 拒绝 | 0 / 0 | 0 / 0 |
| 平均动作数 | 26.0 | 13.6 |
| 总耗时中位数 | 3.665 秒 | 2.784 秒 |
| 总耗时 P95 | 20.397 秒 | 5.887 秒 |
| 名义运动时间中位数 | 16.5 秒 | 21.25 秒 |
| 模型调用 | 0 | 0 |

总耗时包含多次 Python 子进程启动，当前虚拟运动不按物理时间等待。名义运动时间仅按移动距离 / 最大线速度 + 转角 / 最大角速度计算，未计加减速、通信、轮滑和真实控制过程，不能预测实车耗时。

## 5. 复现与数据

```bash
python -m pytest -q --basetemp .pytest-tmp-nav-p1p2-final
python benchmark.py --provider mock --output runs/benchmark-regression-nav-p1p2
python complex_benchmark.py --provider reference --output runs/complex-regression-nav-p1p2
python interactive_benchmark.py --provider reference --output runs/interactive-regression-nav-p1p2
python navigation_benchmark.py --output runs/navigation-fixed-final
python navigation_benchmark.py --random-worlds 20 --seed 20261006 --output runs/navigation-seeded-evaluation
```

提交的 [完整汇总 JSON](navigation_foundation_p1_p2_summary.json) 保存固定和随机场景的逐次指标、汇总及私有场景几何。每次运行的完整观测、单动作 Policy、trace、地图、API surface 和实际 used capabilities 保存在本地结果目录。场景 manifest 供评审和复现使用，没有发送给导航器。

## 6. 限制与下一步

此次证明软件执行链和观测驱动的局部导航可以工作，覆盖范围仍是二维静态世界、理想单束测距和精确姿态。20 个随机场景不能代表开放环境泛化，也没有覆盖动态障碍、掉线、噪声或定位漂移。

U1 使用 78 次动作，接近预算，边界探索仍有明显效率问题；X1 的预算耗尽只证明系统会有限地停下，没有证明它能正确判定全局不可达。单束射线栅格化不保证整格或真实车体通行安全；当前独立 motion guard 继续使用新鲜观测限制实际移动，物理接入需要另行验证。

下一步 P3：让低频模型只输出公开的导航子目标或明确的终止理由，局部导航层持续执行；在同一组固定任务、随机种子和预算下比较模型调用次数、token、端到端延迟、动作数和任务完成率。再增加 P4 噪声、延迟、定位误差和动态障碍。Policy Reuse 继续不实现。

## 7. 文件变化

新增：

- `navigation/__init__.py`、`observation.py`、`grid.py`、`planner.py`、`runner.py`
- `navigation_benchmark.py`
- `task/navigation_benchmark_tasks.py`
- `tests/test_navigation.py`
- 本报告及 `navigation_foundation_p1_p2_summary.json`

修改：

- `agent/observations.py`：结构化观测适配与原有字段兼容。
- `pyproject.toml`：打包模块及导航 benchmark 入口。
- `README.md`：分支策略、导航用法、结果、限制与下一步。
- `.gitignore`：忽略本地运行数据及测试临时目录。

删除：无代码文件；删除两条已经归档的远端分支。
