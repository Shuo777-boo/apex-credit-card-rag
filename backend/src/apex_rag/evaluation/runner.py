"""Real-model experiment runner with raw per-question persistence and failure accounting."""

import json
import platform
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from apex_rag.core.errors import ApexError
from apex_rag.evaluation.metrics import retrieval_metrics, summarize
from apex_rag.ingestion.data import load_official
from apex_rag.retrieval.service import SearchService
from apex_rag.schemas import SearchRequest
from apex_rag.services import Services


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def code_version() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True, timeout=5).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return "unversioned-workspace"


def evaluate(services: Services, config: dict[str, Any], *,
             should_stop: Callable[[], bool] | None = None,
             on_progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    """Evaluate all30 official questions with fresh paid APIs and one fixed deep profile.

    Only query text enters search; labels/gold remain evaluation metadata. Writes config,
    raw per-query/error/ranking/timing, usage and aggregate metrics to DB and disk. Failures
    count as zero quality, retain elapsed evidence and make the run failed. No LLM answers.
    """
    settings, repository = services.settings, services.repository
    _, questions, hashes = load_official(settings.path(settings.data_dir))
    build = repository.get_build(config.get("build_id"))
    repository.validate_ready(build)
    mode = config.get("mode", "hybrid_rerank")
    effective = settings.model_copy(update={"candidate_depth": int(config.get("candidate_depth", 100)),
        "rerank_depth": int(config.get("rerank_depth", 100))})
    # Validate cross-field bounds even when values come from YAML rather than env.
    effective = type(settings).model_validate(effective.model_dump())
    search = SearchService(effective, repository, services.embedding, services.reranker)
    search.graph_search = services.search.graph_search
    run_id = f"core-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    config = {**config, "mode": mode, "strategy": build["config"]["strategy"], "build_id": build["build_id"],
        "models": settings.public_models(), "candidate_depth": effective.candidate_depth,
        "rerank_depth": effective.rerank_depth, "rrf_k": settings.rrf_k, "top_k": 20,
        "dataset_hashes": hashes, "build_config": build["config"], "index_stats": build["stats"],
        "code_version": code_version(), "python": platform.python_version(), "platform": platform.platform(),
        "concurrency": 1, "retries": settings.api_max_retries, "query_cache": False,
        "quantile_method": "numpy linear", "failure_quality": "zero, included in denominator",
        "latency_scope": "successful samples only; failures persist separately; database=dense+sparse sequential roundtrips",
        "p99_caveat": "30 samples: exploratory, not capacity evidence", "started_at": datetime.now(UTC).isoformat()}
    output = settings.path(settings.reports_dir) / "runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    artifacts = {name: str((output / name).relative_to(settings.path(".")) if (output / name).is_relative_to(settings.path(".")) else output / name).replace("\\", "/") for name in ["config.json", "metrics.json", "per_query.jsonl", "provider_calls.json"]}
    write_json(output / "config.json", config)
    repository.save_run(run_id, "running", config, {}, artifacts)
    if on_progress:
        on_progress({"run_id": run_id, "status": "running", "completed_questions": 0, "current_query_id": None})
    results: list[dict[str, Any]] = []
    call_start = len(services.http.calls)
    with (output / "per_query.jsonl").open("w", encoding="utf-8") as raw:
        for question in questions:
            if should_stop and should_stop():
                break
            started = time.perf_counter()
            result: dict[str, Any] = {**question.model_dump(), "ranked_doc_ids": [], "timings_ms": {},
                "error": None, "retrieval": None}
            try:
                # Only the query text and explicit experiment config enter retrieval.
                response = search.search(SearchRequest(query=question.query, mode=mode,
                    top_k=20, build_id=build["build_id"], card_tier=config.get("card_tier")))
                result.update(ranked_doc_ids=[r.doc_id for r in response.results],
                    timings_ms=response.timings_ms.model_dump(), retrieval=response.model_dump(mode="json"))
            except Exception as exc:
                result["error"] = {"code": exc.code if isinstance(exc, ApexError) else "EVALUATION_ERROR",
                    "message": exc.message if isinstance(exc, ApexError) else "Question execution failed"}
                result["failure_elapsed_ms"] = (time.perf_counter() - started) * 1000
            result["metrics"] = retrieval_metrics(result["ranked_doc_ids"], question.relevant_doc_ids)
            if result["retrieval"]:
                stage_coverage = {}
                for stage, items in result["retrieval"]["trace"].items():
                    if items:
                        stage_coverage[stage] = len({x["doc_id"] for x in items} & set(question.relevant_doc_ids)) / len(set(question.relevant_doc_ids))
                result["stage_coverage"] = stage_coverage
            results.append(result)
            raw.write(json.dumps(result, ensure_ascii=False) + "\n")
            raw.flush()
            repository.save_result(run_id, question.query_id, result)
            write_json(output / "provider_calls.json", services.http.calls[call_start:])
            if on_progress:
                on_progress({"run_id": run_id, "status": "running", "completed_questions": len(results), "current_query_id": question.query_id})
            if result["error"] and result["error"]["code"] in {"JOB_CANCELLED", "JOB_TIMEOUT", "MODEL_BUDGET_EXCEEDED"}:
                break
    metrics = summarize(results)
    status = "completed" if not metrics["counts"]["failed"] else "failed"
    if len(results) < len(questions) or (should_stop and should_stop()):
        status = "cancelled" if should_stop and should_stop() else "failed"
    metrics["execution"] = {"planned_questions": len(questions), "attempted_questions": len(results), "partial": len(results) != len(questions)}
    write_json(output / "metrics.json", metrics)
    write_json(output / "provider_calls.json", services.http.calls[call_start:])
    repository.save_run(run_id, status, config, metrics, artifacts)
    return {"run_id": run_id, "status": status, "metrics": metrics, "artifacts": artifacts}


def report(services: Services, run_id: str) -> dict[str, str]:
    """Render a static Markdown run from persisted evidence without paid API calls."""
    detail = services.repository.get_evaluation(run_id).model_dump(mode="json", by_alias=True)
    config, metrics = detail["config"], detail["metrics"]
    lines = [f"# Core run {run_id}", "", f"Status: {detail['status']}. Strategy: {config['strategy']}; mode: {config['mode']}.",
        "", "| Metric | Value |", "|---|---:|"]
    lines += [f"| {key} | {value:.6f} |" for key, value in metrics.items() if isinstance(value, float | int)]
    lines += ["", "Latency milliseconds (successful samples only; failures retained separately):", "", "```json", json.dumps(metrics.get("latency_ms"), indent=2), "```", "", "30 fixed questions produce exploratory P99 only. Full retrieval includes external model calls; database excludes them. No automatic sub-100ms claim.", "", "Precision uses k as denominator even with fewer results; binary nDCG uses log2(rank+1); MRR is document-deduplicated at 20. Failed questions count as zero quality.", "", "Known annotation issue Q-029 and distinct grace-period concepts DOC-0039/DOC-0136 require manual review; original labels unchanged."]
    path = services.settings.path(services.settings.reports_dir) / "runs" / run_id / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"report": str(path)}


def combined_report(services: Services, runs: list[dict[str, Any]]) -> str:
    """Render saved real runs, all metrics, paired changes/cases and comparison PNG.

    Does not create or alter ranks. Missing latency samples plot as gaps, not measured zero.
    Writes reports/results.md and core-comparison.png; no external model requests.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    lines = ["# Core six-experiment results", "", "All rankings below come from real API and database execution. Failures remain in the denominator.", "", "| Strategy | Mode | Recall@5 | Precision@5 | nDCG@5 | MRR@20 | DB P50/P99 ms | Full P50/P99 ms | Errors |", "|---|---|---:|---:|---:|---:|---|---|---:|"]
    for run in runs:
        detail = services.repository.get_evaluation(run["run_id"])
        metric = run["metrics"]
        db = metric["latency_ms"]["database"]
        full = metric["latency_ms"]["retrieval_total"]
        lines.append(f"|{detail.config['strategy']}|{detail.config['mode']}|{metric['recall@5']:.4f}|{metric['precision@5']:.4f}|{metric['ndcg@5']:.4f}|{metric['mrr@20']:.4f}|{db['p50']}/{db['p99']}|{full['p50']}/{full['p99']}|{metric['counts']['failed']}|")
    lines += ["", "## Limits and interpretation", "", "Full retrieval includes fresh query embedding and real reranking. Thirty queries do not establish stable P99 or production capacity. Compare both improved and degraded outcomes; the benchmark is not an independent tuning holdout. Database latency is sequential dense+FTS application roundtrip, not model-inclusive latency. The original 150 documents are short policies, so hierarchy may retain most Details nodes intact. Official labels and gold answers are never retrieval inputs.", "", "Q-029 labels mainly describe Platinum while asking about Preferred Travel; retain and disclose this mismatch. DOC-0039 and DOC-0136 discuss different grace concepts and years; do not infer universal supersession.", "", "## Runs", ""]
    lines += [f"- [{r['run_id']}](runs/{r['run_id']}/report.md) ({r['status']})" for r in runs]
    lines += ["", "## Full quality metrics", "", "| Strategy / mode | " + " | ".join(retrieval_metrics([], ["reference"]).keys()) + " |", "|---|" + "---:|" * 10]
    for run in runs:
        detail = services.repository.get_evaluation(run["run_id"])
        lines.append(f"|{detail.config['strategy']} / {detail.config['mode']}|" + "|".join(f"{run['metrics'][k]:.6f}" for k in retrieval_metrics([], ["reference"])) + "|")
    lines += ["", "## Paired changes and diagnostic cases", "", "These cases describe saved real runs; original labels remain unchanged. Candidate coverage is stored per query for dense/sparse/fusion/rerank."]
    for strategy in ("fixed_window", "policy_hierarchy"):
        group = [services.repository.get_evaluation(r["run_id"]) for r in runs
            if services.repository.get_evaluation(r["run_id"]).config["strategy"] == strategy]
        baseline = next((d for d in group if d.config["mode"] == "dense"), None)
        enhanced = next((d for d in group if d.config["mode"] == "hybrid_rerank"), None)
        if baseline and enhanced:
            before = {q.query_id: q for q in baseline.per_query}
            after = {q.query_id: q for q in enhanced.per_query}
            delta = {key: after[key].metrics["ndcg@5"] - before[key].metrics["ndcg@5"] for key in before}
            improved = [key for key, difference in delta.items() if difference > 0]
            degraded = [key for key, difference in delta.items() if difference < 0]
            lines += ["", f"### {strategy}", "", f"Improved nDCG@5 questions: {improved or 'none'}. Degraded: {degraded or 'none'}.", "", "| Metric | Enhanced minus Dense (absolute) |", "|---|---:|"]
            for key in retrieval_metrics([], ["reference"]):
                lines.append(f"|{key}|{sum(q.metrics[key] for q in enhanced.per_query)/len(enhanced.per_query)-sum(q.metrics[key] for q in baseline.per_query)/len(baseline.per_query):+.6f}|")
            for identity, theme in [("Q-002", "same topic, different card"), ("Q-004", "multiple relevant documents"), ("Q-012", "numeric boundary and deductible"), ("Q-029", "annotation evidence/product mismatch"), ("Q-009", "payment cutoff and policy concepts")]:
                q = after.get(identity)
                if q:
                    lines += ["", f"- {identity} ({theme}): {q.query} Labels {q.relevant_doc_ids}; Dense {before[identity].ranked_doc_ids[:5]}; Enhanced {q.ranked_doc_ids[:5]}; delta nDCG@5 {delta[identity]:+.4f}; error {q.error}."]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4), constrained_layout=True)
    labels = []
    for run in runs:
        detail = services.repository.get_evaluation(run["run_id"])
        labels.append(f"{detail.config['strategy']}\n{detail.config['mode']}")
    x = np.arange(len(runs))
    axes[0].bar(x, [r["metrics"]["recall@5"] for r in runs], label="Recall@5")
    axes[0].plot(x, [r["metrics"]["ndcg@5"] for r in runs], "o-", color="darkorange", label="nDCG@5")
    axes[0].set_ylim(0, 1)
    axes[0].set_ylabel("Document relevance quality")
    axes[0].legend()
    axes[1].bar(x, [r["metrics"]["latency_ms"]["retrieval_total"]["p50"] if r["metrics"]["latency_ms"]["retrieval_total"]["p50"] is not None else float("nan") for r in runs], label="Full retrieval P50")
    axes[1].set_ylabel("Milliseconds (external APIs included)")
    axes[1].legend()
    for axis in axes:
        axis.set_xticks(x, labels, rotation=30, ha="right", fontsize=7)
    figure_path = services.settings.path(services.settings.reports_dir) / "core-comparison.png"
    fig.savefig(figure_path, dpi=180)
    plt.close(fig)
    lines += ["", "![Quality and full retrieval latency](core-comparison.png)"]
    path = services.settings.path(services.settings.reports_dir) / "results.md"
    lines += ["", "## Index experiments", "", "Separate warmed database benchmarks exclude external APIs; full retrieval above includes them. See raw plans/measurements and graphs below. No serving index is replaced by these temporary-table experiments."]
    for build_id in dict.fromkeys(services.repository.get_evaluation(r["run_id"]).config["build_id"] for r in runs):
        benchmark_path = path.parent / "index-benchmark" / f"{build_id}.json"
        if benchmark_path.is_file():
            benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
            lines += ["", f"### {build_id}", "", "| m | construction | search | build ms | bytes | ANN Recall@20 | Default DB P50/P99 ms | Forced DB P50/P99 ms |", "|---:|---:|---:|---:|---:|---:|---|---|"]
            for row in benchmark["results"]:
                normal, forced = row["default_latency_ms"], row["forced_index_latency_ms"]
                lines.append(f"|{row['m']}|{row['ef_construction']}|{row['ef_search']}|{row['build_ms']:.3f}|{row['index_bytes']}|{row['ann_recall@20']:.5f}|{normal['p50']:.3f}/{normal['p99']:.3f}|{forced['p50']:.3f}/{forced['p99']:.3f}|")
            lines += ["", f"Exact reference: {benchmark['exact_latency_ms']}; ordinary sample order and raw timings are persisted, EXPLAIN overhead excluded.", "", f"[Raw evidence](index-benchmark/{build_id}.json)", "", f"![Index tradeoffs](index-benchmark/{build_id}.png)"]
    lines += ["", "## Acceptance limitations", "", "Complete retrieval below100ms is not established by database-only performance. If the measured model-inclusive numbers exceed100ms, that requirement remains unsatisfied. Thirty fixed queries produce exploratory P99, not production capacity evidence. No human faithfulness review is implied by citation-ID checks. Per-run configs preserve actual data/model/index/software settings; unknown model usage or billed cost must stay unavailable rather than invented."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)
