# 实验报告索引

模拟成绩、模型文本规划成绩和实车反馈验证采用不同协议，不能合并成一个成功率。每份报告说明自己的任务、预算、观测来源与限制；JSON/CSV是对应批次的整理结果，不是全量本地运行日志。

| 日期/阶段 | 报告 | 数据与重点 |
| --- | --- | --- |
| 2026-10-13 调研 | [相似项目与优化路线](related_projects_and_roadmap_2026-10-13.md) | 15个项目/组件、一手来源、当前缺口、分阶段建议；非新增实现或实车成绩 |
| 2026-10-12 实车 | [ESP32实车集成与两段反馈流程](physical_integration_2026-10-12.md) | 用户提供的终端记录；编码器/相对陀螺仪估计，无独立地面真值 |
| 2026-10-07 规划提示词 | [提示词对照](planner_prompt_ablation_2026-10-07.md) | `planner_prompt_ablation_2026-10-07_summary.json`、`…_trials.csv` |
| 2026-10-07 模型对照 | [Qwen3.7 Flash与DeepSeek Flash](qwen37_deepseek_full_2026-10-07.md) | `qwen37_deepseek_full_2026-10-07_summary.json`、`…_episodes.csv` |
| P4 扰动 | [静态观测/执行扰动](navigation_robustness_p4.md) | [JSON](navigation_robustness_p4_summary.json)，估计位姿与私有真值分离 |
| P3 多目标规划 | [低频规划与持续导航](p3_mission_planning.md) | [JSON](p3_mission_planning_summary.json)，hybrid/direct/oracle分开 |
| P1/P2 导航 | [观测地图与确定性导航](navigation_foundation_p1_p2.md) | [JSON](navigation_foundation_p1_p2_summary.json)，零模型导航成绩 |
| 2026-10-06 全量复测 | [DeepSeek复测](deepseek_full_rerun_2026-10-06.md) | [JSON](deepseek_full_rerun_summary.json) |
| 2026-10-05 隐藏地图交互 | [交互优化](interactive_optimization_2026-10-05.md) | [JSON](interactive_optimization_2026-10-05_summary.json) |
| 2026-10-05 模型基础 | [DeepSeek软件评测](deepseek_flash_2026-10-05.md) | [JSON](deepseek_flash_2026-10-05_summary.json) |

复测脚本见[scripts索引](../scripts/README.md)，入口及参数见[模拟指南](../docs/SIMULATION_GUIDE.md)。历史报告中的196项测试是当时结果，不是当前代码测试数量。
