这些测试数据摘自本项目已保存报告的只读 HTTP GET 快照；不是应用默认数据，不参与页面运行或真实成绩计算。

- core.json：core-20261109T183355Z-a778d591，保留一个真实逐题结果。
- graph.json：graph-20261109T142652-fd3c9427，保留无路径和实际非空路径样本。
- judge-final.json：judge-20261109T173046-0e2f4df9，保留含多条支持引文的 Q-002:dense 和 Q-001:dense；verdicts 顺序与 samples 相反，用于验证按 sample_id 关联。
- judge-old.json：judge-20261109T142820-9c68f65f，保留旧失败样本及其 null 评分。

汇总 metrics 保持原报告值，裁剪样本用于验证结构和交互，不能由裁剪数组重算或认定报告完整性。完整运行报告保存在 reports/bonus-runs/ 对应目录。null 指标测试为明确构造的边界测试，非真实得分。

- evaluation-options.json：两种就绪构建和六组评测选项，用于对比任务交互测试。
