# 实验报告

历史评测任务、实验结果和运行报告已清空，当前保留清理后新生成的一组实验：

| 实验 | 分块 / 模式 | 完成情况 | Recall@5 | nDCG@5 | MRR@20 | 完整检索 P50 / P99 |
| --- | --- | --- | --- | --- | --- | --- |
| [core-20261111T042028Z-b9ae9ae8](runs/core-20261111T042028Z-b9ae9ae8/report.md) | policy_hierarchy / dense | 30/30，失败0 | 0.977778 | 0.928179 | 0.919444 | 322.11 / 754.76 ms |

同目录保存配置、原始指标、逐题排名和模型调用记录。这是单组结果，不是六组前后对比；30题的P99仅作探索，完整检索包含在线模型调用，尚未达到100ms。

完整对比可按根目录 README 执行 `docker compose --profile tools run --build --rm reproduce`，会调用真实模型并生成 `reports/results.md`、图表和新run。查看本目录文件不触发模型调用。本机任务状态目录 `reports/jobs/` 不纳入版本控制。

知识库、向量索引、标准评测题和配置均保留。core-evidence/pricing-r9 是静态官方价格来源资料。
