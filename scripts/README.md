# 辅助脚本

运行位置为仓库根目录。主控、Benchmark和实车入口仍在根目录，避免修改既有包入口。

| 脚本 | 用途 |
| --- | --- |
| `check_repository.py` | 检查本地文档链接、固件归档哈希和第三方许可；不访问网络或硬件 |
| `compare_flash_benchmarks.py` | 既有Qwen/DeepSeek全套对照复测 |
| `analyze_flash_comparison.py` | 整理模型对照结果 |
| `verify_planner_prompts.py` | 既有规划提示词对照验证 |
| `report_planner_prompts.py` | 整理规划提示词实验结果 |
| `analyze_navigation_optimization.py` | 导航优化实验分析 |
| `run_robustness_shards.py` | 分片运行扰动实验 |

调用真实模型的复测需要用户自行配置服务与凭证，可能产生费用；普通pytest和`check_repository.py`不会调用模型。具体命令与结果协议见[模拟指南](../docs/SIMULATION_GUIDE.md)和[报告索引](../reports/README.md)。
