# 扩展实验

扩展使用核心数据构建、模型适配器与原文证据，结果不混入官方30题检索成绩。以下命令在项目根目录、已激活 Conda 环境且已完成 bootstrap 后执行。Graph 和 Judge 会调用真实模型，先核对配置的地域、预算及模型权限。

| 方向 | 命令 | 报告 |
| --- | --- | --- |
| Iceberg 向量快照与查询 | `python -m apex_rag.cli bonus-iceberg --config configs/bonus-iceberg.yaml` | [iceberg.md](../reports/iceberg.md) |
| 图谱来源审计 | `python -m apex_rag.cli bonus-graph-audit --config configs/bonus-graph.yaml` | 新建图谱审计运行目录 |
| Graph 与基础检索对照 | `python -m apex_rag.cli bonus-graph --config configs/bonus-graph.yaml` | [graphrag.md](../reports/graphrag.md) |
| 自动回答质量评估 | `python -m apex_rag.cli bonus-judge --config configs/bonus-judge.yaml` | [judge.md](../reports/judge.md) |

运行产物位于 `reports/bonus-runs/<run_id>/`：配置、摘要、逐题样本、模型调用、预算及人工核对材料。报告接口只读，打开网页不会自动执行扩展。

## Iceberg

使用 PyIceberg、Arrow 与 DuckDB。通过有效 snapshot / manifest 读取数据，精确余弦排序，并与 pgvector exact 结果对照。保留更新与时间回溯示例。它展示数据湖版本管理，不提供原生ANN，也未证明千万级在线延迟。

## GraphRAG

从文档元数据及逐字原句建立产品、政策、费用、权益与来源关系，保留 doc_id、原文和hash。以混合检索种子进行有限两跳扩展，候选受数量限制并进入相同重排。独立六题覆盖和引用指标是完整性代理；人工标注与回答语义完整性尚未确认。

## LLM Judge

上下文相关性逐证据打分，回答忠实度按提取出的原子主张判断。结构错误、无效引用和失败样本显式记录，不以HTTP成功代替评审成功。同模型自评及主张遗漏可能造成偏差。

保存的v2.2与恢复阶段使用相同原始60个回答，最终有效自动评审60/60；旧失败与基础结果保留，不覆盖历史。失败项额外尝试不能被描述成统一单轮实验。人工复核待完成。

复用保存回答的新方法计划：

```bash
python -m apex_rag.cli bonus-judge-rejudge --config configs/bonus-judge-v2.2.yaml
```

默认仅生成预算计划；付费执行参数以CLI帮助为准。历史版本、来源run与提示词hash是追溯信息，不能作为质量保证。

## 验证

```bash
python -m pytest backend/tests/extensions -q
```

夹具检验编排、真实本地Iceberg文件与错误处理，不代替真实模型或生产性能证据。
