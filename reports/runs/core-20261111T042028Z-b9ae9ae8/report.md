# Core run core-20261111T042028Z-b9ae9ae8

Status: completed. Strategy: policy_hierarchy; mode: dense.

| Metric | Value |
|---|---:|
| recall@1 | 0.761111 |
| recall@3 | 0.922222 |
| recall@5 | 0.977778 |
| precision@1 | 0.866667 |
| precision@3 | 0.400000 |
| precision@5 | 0.273333 |
| ndcg@1 | 0.866667 |
| ndcg@3 | 0.894495 |
| ndcg@5 | 0.928179 |
| mrr@20 | 0.919444 |

Latency milliseconds (successful samples only; failures retained separately):

```json
{
  "database": {
    "p50": 2.2591954993913532,
    "p99": 23.69489500009879,
    "samples": 30
  },
  "retrieval_total": {
    "p50": 322.10718499936775,
    "p99": 754.7620196109896,
    "samples": 30
  }
}
```

30 fixed questions produce exploratory P99 only. Full retrieval includes external model calls; database excludes them. No automatic sub-100ms claim.

Precision uses k as denominator even with fewer results; binary nDCG uses log2(rank+1); MRR is document-deduplicated at 20. Failed questions count as zero quality.

Known annotation issue Q-029 and distinct grace-period concepts DOC-0039/DOC-0136 require manual review; original labels unchanged.
