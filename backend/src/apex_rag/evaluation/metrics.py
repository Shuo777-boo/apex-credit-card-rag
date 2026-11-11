"""Binary document metrics and explicit linear-interpolation latency quantiles."""

import math
from typing import Any

import numpy as np


def retrieval_metrics(ranked_doc_ids: list[str], relevant: list[str]) -> dict[str, float]:
    """Compute unique-document binary Recall/Precision/nDCG@1/3/5 and MRR@20.

    Precision denominator is k even for short results; recall uses all unique labels.
    nDCG discounts log2(rank+1). Empty labels are invalid; empty rankings score zero.
    """
    if not relevant:
        raise ValueError("Relevant document labels must be nonempty")
    ranked = list(dict.fromkeys(ranked_doc_ids))
    labels = set(relevant)
    metrics: dict[str, float] = {}
    for k in (1, 3, 5):
        hits = sum(doc_id in labels for doc_id in ranked[:k])
        metrics[f"recall@{k}"] = hits / len(labels)
        metrics[f"precision@{k}"] = hits / k
        dcg = sum(1 / math.log2(rank + 1) for rank, doc_id in enumerate(ranked[:k], 1) if doc_id in labels)
        ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(k, len(labels)) + 1))
        metrics[f"ndcg@{k}"] = dcg / ideal
    metrics["mrr@20"] = next((1 / rank for rank, doc_id in enumerate(ranked[:20], 1) if doc_id in labels), 0)
    return metrics


def quantiles(values: list[float]) -> dict[str, Any]:
    """Return linear-interpolated P50/P99 in input units; empty samples stay null."""
    return {"p50": float(np.quantile(values, .5, method="linear")) if values else None,
            "p99": float(np.quantile(values, .99, method="linear")) if values else None,
            "samples": len(values)}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Macro-average quality over all questions including explicit failed zero scores.

    Sequential dense+sparse roundtrips form DB milliseconds. Complete retrieval includes
    external APIs. Latency quantiles use successes only and disclose the failure count.
    """
    keys = retrieval_metrics([], ["placeholder"]).keys()
    # Failed questions carry zero quality, remain in the denominator and separate error count.
    metrics: dict[str, Any] = {key: sum(r["metrics"][key] for r in results) / len(results) if results else 0 for key in keys}
    database = [sum(r["timings_ms"].get(stage) or 0 for stage in ("dense", "sparse")) for r in results if not r["error"]]
    full = [r["timings_ms"]["retrieval_total"] for r in results if not r["error"]]
    failed = sum(bool(r["error"]) for r in results)
    metrics["counts"] = {"total": len(results), "succeeded": len(results) - failed, "failed": failed}
    metrics["latency_ms"] = {"database": quantiles(database), "retrieval_total": quantiles(full)}
    return metrics
