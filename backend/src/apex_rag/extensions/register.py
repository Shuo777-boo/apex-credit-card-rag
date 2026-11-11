"""The only extension wiring point called by the core CLI and API owners."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _root(services: Any) -> Path:
    return services.settings.path(".")


def _reports(services: Any) -> Path:
    return services.settings.path(services.settings.reports_dir) / "bonus-runs"


def _config(path: Path) -> dict[str, Any]:
    import yaml

    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Extension configuration must be a mapping.")
    return value


def _save(path: Path, value: Any) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return value


def _new_run(services: Any, kind: str) -> Path:
    return _reports(services) / f"{kind}-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"


def capabilities(services: Any) -> dict[str, Any]:
    """Non-paid capability discovery is distinct from an active ready build."""
    graph = importlib.util.find_spec("networkx") is not None
    iceberg = all(
        importlib.util.find_spec(name) is not None for name in ("pyiceberg", "pyarrow", "duckdb")
    )
    return {
        "graph": {
            "available": graph,
            "mode": "graph_enhanced",
            "max_hops": 2,
            "requires_ready_build": True,
        },
        "iceberg": {"available": iceberg, "algorithm": "exact cosine scan"},
        "judge": {
            "available": True,
            "requires_key": True,
            "requires_explicit_cli_run": True,
            "human_review_required": 10,
        },
        "report_schema_version": "bonus-v1",
    }


def _install_hooks(services: Any) -> None:
    if importlib.util.find_spec("networkx") is not None:
        from apex_rag.graph.engine import GraphSearch

        services.search.graph_search = GraphSearch(services)
    services.extension_reproduce = lambda config: reproduce_extensions(services, config)


def graph_audit(services: Any, config: dict[str, Any]) -> dict[str, Any]:
    from apex_rag.graph.engine import audit, build_graph, expand

    docs = services.repository.list_documents()
    graph = build_graph(docs)
    run = _new_run(services, "graph")
    report = audit(graph, docs)
    examples = []
    queries = json.loads(
        services.settings.path(config.get("queries", "data/challenge_queries.json")).read_text(
            encoding="utf-8"
        )
    )
    for query in queries:
        examples.append(
            {
                "query_id": query["query_id"],
                "query": query["query"],
                "support_doc_ids": query["support_doc_ids"],
                "path_example": expand(graph, query["support_doc_ids"][:1], query["query"]),
            }
        )
    # Label source-annotated path examples explicitly; these are not retrieval results.
    report.update(
        {
            "status": "source_audit_only",
            "examples": examples,
            "examples_use_annotated_seeds": True,
            "retrieval_comparison": "not_run",
        }
    )
    _save(run / "summary.json", report)
    from networkx.readwrite import json_graph

    _save(run / "graph.json", json_graph.node_link_data(graph, edges="edges"))
    return report


def graph_compare(services: Any, config: dict[str, Any]) -> dict[str, Any]:
    """A separate challenge set, same core reranker; citation coverage is not semantic completeness."""
    import hashlib

    from apex_rag.evaluation.judge.runner import (
        Budget,
        append_json,
        capture_usage,
        usage_summary,
        validate_paid_bounds,
    )
    from apex_rag.generation.service import SYSTEM_PROMPT
    from apex_rag.schemas import SearchRequest

    queries = json.loads(
        services.settings.path(config.get("queries", "data/challenge_queries.json")).read_text(
            encoding="utf-8"
        )
    )
    run = _new_run(services, "graph")
    run.mkdir(parents=True)
    (run / "generation_prompt.txt").write_text(SYSTEM_PROMPT, encoding="utf-8")
    budget = Budget(run, config["budget"])
    records = []
    build_id = services.repository.get_build()["build_id"]
    validate_paid_bounds(services, config, queries, build_id)
    _save(
        run / "config.json",
        {
            "config": config,
            "build_id": build_id,
            "models": services.settings.public_models(),
            "comparison": "paired six independent challenges; sequential modes; small sample and order/cache effects possible",
            "generation_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
            "generation_parameters": {"temperature": 0, "max_tokens": 2400, "top_k_documents": 5},
        },
    )
    for query in queries:
        for mode in ("hybrid_rerank", "graph_enhanced"):
            usage_start = len(getattr(getattr(services, "http", None), "calls", []))
            try:
                budget.reserve("answer_" + mode, f"{query['query_id']}:{mode}")
                response = services.answer.answer(
                    SearchRequest(query=query["query"], mode=mode, top_k=5, build_id=build_id)
                )
                data = response.model_dump(mode="json")
                needed = set(query["support_doc_ids"])
                retrieved = {r["doc_id"] for r in data["retrieval"]["results"]}
                cited = {c["doc_id"] for c in data["citations"]}
                item = {
                    "query_id": query["query_id"],
                    "query": query["query"],
                    "mode": mode,
                    "status": "completed",
                    "document_coverage": len(needed & retrieved) / len(needed),
                    "citation_coverage_proxy": len(needed & cited) / len(needed),
                    "manual_answer_completeness": None,
                    "response": data,
                    "required_documents": sorted(needed),
                }
            except Exception as exc:
                item = {
                    "query_id": query["query_id"],
                    "mode": mode,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }
            append_json(run / "samples.jsonl", item)
            capture_usage(services, run, f"{query['query_id']}:{mode}", usage_start, budget)
            records.append(item)
    summary = {}
    for mode in ("hybrid_rerank", "graph_enhanced"):
        items = [i for i in records if i["mode"] == mode and i["status"] == "completed"]
        summary[mode] = {
            "expected": len(queries),
            "completed": len(items),
            "document_coverage": sum(i["document_coverage"] for i in items) / len(items)
            if items
            else None,
            "citation_coverage_proxy": sum(i["citation_coverage_proxy"] for i in items) / len(items)
            if items
            else None,
            "mean_retrieval_ms": sum(
                i["response"]["retrieval"]["timings_ms"]["retrieval_total"] for i in items
            )
            / len(items)
            if items
            else None,
            "manual_answer_completeness": None,
        }
    base, enhanced = (summary[m]["mean_retrieval_ms"] for m in ("hybrid_rerank", "graph_enhanced"))
    report = {
        "status": "completed_pending_human"
        if all(summary[m]["completed"] == len(queries) for m in summary)
        else "incomplete",
        "metrics": summary,
        "additional_retrieval_ms": enhanced - base
        if base is not None and enhanced is not None
        else None,
        "samples": records,
        "official_metrics": False,
        "challenge_human_confirmed": all(
            q.get("human_confirmed") is True and q.get("human_reviewer") for q in queries
        ),
        "note": "Citation coverage is a proxy only. Human review must check every required condition in the answer.",
    }
    review = [
        {
            "query_id": r["query_id"],
            "mode": r["mode"],
            "reviewer": None,
            "reviewed_at": None,
            "status": "pending_human",
            "checklist": next(
                q["completeness_checklist"] for q in queries if q["query_id"] == r["query_id"]
            ),
            "supported_and_answered": None,
            "notes": None,
        }
        for r in records
        if r["status"] == "completed"
    ]
    report["human_review"] = review
    report["provider_cost_summary"] = usage_summary(run)
    report["budget"] = json.loads(budget.path.read_text(encoding="utf-8"))
    _save(run / "human-review.json", review)
    _save(run / "human-annotations.json", queries)
    packet = [
        "# GraphRAG 人工核对材料",
        "",
        "具名人类确认human-annotations.json支持文档，然后填写human-review.json；AI生成待填表不算人工复核。",
        "supported_and_answered按checklist逐项填布尔值，记录reviewer、带时区reviewed_at、notes和status=completed。",
        "",
    ]
    for query in queries:
        packet.extend(["## " + query["query_id"], "", query["query"], "", "### 支持原文", ""])
        for source in query["support"]:
            packet.extend(["- " + source["doc_id"], "", source["quote"], ""])
        packet.extend(
            ["### 完整性检查项", ""]
            + [f"{i + 1}. {text}" for i, text in enumerate(query["completeness_checklist"])]
            + [""]
        )
        for sample in records:
            if sample["query_id"] == query["query_id"]:
                packet.extend(
                    [
                        "### " + sample["mode"],
                        "",
                        sample.get("response", {}).get("answer", "生成失败"),
                        "",
                    ]
                )
    (run / "human-review-packet.md").write_text("\n".join(packet), encoding="utf-8")
    return _save(run / "summary.json", report)


def finalize_graph_review(directory: Path) -> dict[str, Any]:
    """Require real human completeness judgments for both modes and every challenge."""
    report = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    review = json.loads((directory / "human-review.json").read_text(encoding="utf-8"))
    annotations = json.loads((directory / "human-annotations.json").read_text(encoding="utf-8"))
    if not annotations or not all(
        q.get("human_confirmed") is True and q.get("human_reviewer") for q in annotations
    ):
        raise ValueError("Challenge support annotations must first be confirmed by a named human.")
    required = {
        s["query_id"]: set(s["required_documents"])
        for s in report["samples"]
        if s["status"] == "completed"
    }
    if any(set(q["support_doc_ids"]) != required.get(q["query_id"]) for q in annotations):
        raise ValueError(
            "Support labels changed; rerun comparison if the benchmark definition changes."
        )
    report["challenge_human_confirmed"] = True
    expected = {(s["query_id"], s["mode"]) for s in report["samples"]}
    if len(review) != len(expected) or {(r["query_id"], r["mode"]) for r in review} != expected:
        raise ValueError("Every paired challenge sample requires one review.")
    for item in review:
        values = item["supported_and_answered"]
        if item.get("status") != "completed" or not item.get("reviewer") or not item.get("notes"):
            raise ValueError("Named human reviewer, completion status and notes are required.")
        if datetime.fromisoformat(item["reviewed_at"]).tzinfo is None:
            raise ValueError("Review time requires a timezone.")
        if (
            not isinstance(values, list)
            or len(values) != len(item["checklist"])
            or any(type(v) is not bool for v in values)
        ):
            raise ValueError(
                "Each required checklist condition needs a boolean supported-and-answered decision."
            )
        item["completeness"] = sum(values) / len(values)
    for mode in ("hybrid_rerank", "graph_enhanced"):
        values = [r["completeness"] for r in review if r["mode"] == mode]
        report["metrics"][mode]["manual_answer_completeness"] = sum(values) / len(values)
    report["human_review"] = review
    if all(
        report["metrics"][m]["completed"] == report["metrics"][m]["expected"]
        for m in report["metrics"]
    ):
        report["status"] = "completed"
    return _save(directory / "summary.json", report)


def run_extension(services: Any, kind: str, config_path: str | None = None) -> dict[str, Any]:
    config = _config(services.settings.path(config_path or f"configs/bonus-{kind}.yaml"))
    if kind in {"graph", "judge"} and not services.settings.dashscope_api_key.get_secret_value():
        from apex_rag.core.errors import ApexError

        raise ApexError(
            "PROVIDER_NOT_CONFIGURED",
            "DASHSCOPE_API_KEY is required for real extension experiments",
        )
    if kind == "iceberg":
        from apex_rag.experiments.iceberg.poc import run_poc

        run = _new_run(services, kind)
        report = run_poc(services, run, top_k=config.get("top_k", 5))
        return _save(run / "summary.json", report)
    if kind == "judge":
        from apex_rag.evaluation.judge.runner import run_judge

        config["queries"] = str(
            services.settings.path(config.get("queries", "data/eval_queries.json"))
        )
        return run_judge(services, config, _new_run(services, kind))
    if kind == "graph":
        return graph_compare(services, config)
    if kind == "graph-audit":
        return graph_audit(services, config)
    raise ValueError("Unknown extension kind.")


def reproduce_extensions(services: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Explicitly requested experiments propagate failure to the core workflow."""
    output = {}
    for kind, settings in config.items():
        if kind not in {"iceberg", "graph", "judge"}:
            raise ValueError(f"Unknown bonus experiment: {kind}")
        if settings is False or settings is None:
            continue
        config_path = settings.get("config") if isinstance(settings, dict) else None
        result = run_extension(services, kind, config_path)
        output[kind] = result
        if result["status"] != "completed":
            raise RuntimeError(
                f"{kind} is incomplete or requires human verification; inspect saved report."
            )
    return output


def register_cli(parser: argparse.ArgumentParser, services: Any) -> None:
    """Register CLI commands without I/O or paid requests at registration/import time."""
    _install_hooks(services)
    subparsers = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None
    )
    if subparsers is None:
        subparsers = parser.add_subparsers(dest="command", required=True)
    for kind in ("iceberg", "graph", "graph-audit", "judge"):
        name = f"bonus-{kind}"
        if name in subparsers.choices:
            continue
        child = subparsers.add_parser(name)
        child.add_argument("--config", default=f"configs/bonus-{kind.removesuffix('-audit')}.yaml")

        def handler(args: argparse.Namespace, chosen: str = kind) -> Any:
            result = run_extension(services, chosen, args.config)
            if result["status"] in {"incomplete", "failed"}:
                raise RuntimeError("Experiment incomplete; inspect persisted raw artifacts.")
            return result

        child.set_defaults(handler=handler)
    if "bonus-judge-review" in subparsers.choices:
        return
    review = subparsers.add_parser("bonus-judge-review")
    review.add_argument("--report-id", required=True)

    def review_handler(args: argparse.Namespace) -> Any:
        from apex_rag.evaluation.judge.runner import finalize_review

        if not re.fullmatch(r"judge-\d{8}T\d{6}-[a-f0-9]{8}", args.report_id):
            raise ValueError("Invalid Judge report ID.")
        return finalize_review(_reports(services) / args.report_id)

    review.set_defaults(handler=review_handler)
    repair = subparsers.add_parser("bonus-judge-resume")
    repair.add_argument("--report-id", required=True)
    repair.add_argument("--generation-prompt", required=True)

    def repair_handler(args: argparse.Namespace) -> Any:
        from apex_rag.evaluation.judge.resume import resume_failed

        if not re.fullmatch(r"judge-\d{8}T\d{6}-[a-f0-9]{8}", args.report_id):
            raise ValueError("Invalid Judge report ID.")
        result = resume_failed(
            services,
            _reports(services) / args.report_id,
            services.settings.path(args.generation_prompt),
        )
        if result["status"] == "incomplete":
            raise RuntimeError(
                "Bounded repair finished with missing/failed tasks; inspect artifacts."
            )
        return result

    repair.set_defaults(handler=repair_handler)
    v2 = subparsers.add_parser("bonus-judge-rejudge")
    v2.add_argument("--config", default="configs/bonus-judge-v2.2.yaml")
    v2.add_argument("--execute", action="store_true")
    v2.add_argument("--pilot", action="store_true")
    v2.add_argument("--resume-report-id")

    def v2_handler(args: argparse.Namespace) -> Any:
        from apex_rag.evaluation.judge.rejudge import plan, run

        config = _config(services.settings.path(args.config))
        source_id = config.get("source_report_id", "")
        if not re.fullmatch(r"judge-\d{8}T\d{6}-[a-f0-9]{8}", source_id):
            raise ValueError("Invalid original Judge report ID.")
        source = _reports(services) / source_id
        if not args.execute:
            return plan(services, source, config)
        if not services.settings.dashscope_api_key.get_secret_value():
            raise ValueError("Actual Judge execution requires a configured API key.")
        resume_id = args.resume_report_id
        if resume_id and not re.fullmatch(r"judge-\d{8}T\d{6}-[a-f0-9]{8}", resume_id):
            raise ValueError("Invalid resumed v2 Judge report ID.")
        directory = _reports(services) / resume_id if resume_id else _new_run(services, "judge")
        result = run(
            services,
            source,
            directory,
            config,
            selected_ids=config["pilot_ids"] if args.pilot else None,
            resume=bool(resume_id),
        )
        if args.pilot and result["pilot_status"] != "completed":
            raise RuntimeError("Pilot incomplete; stop and inspect before any full run.")
        if result["status"] == "incomplete" and not args.pilot:
            raise RuntimeError("Independent v2 evaluation incomplete; inspect raw records.")
        return result

    v2.set_defaults(handler=v2_handler)
    recovery = subparsers.add_parser("bonus-judge-recover")
    recovery.add_argument("--base-report-id", required=True)
    recovery.add_argument("--execute", action="store_true")
    recovery.add_argument("--expected-failures", type=int)
    recovery.add_argument("--max-cost", type=float)
    recovery.add_argument("--resume-report-id")

    def recovery_handler(args: argparse.Namespace) -> Any:
        from apex_rag.evaluation.judge.recovery import plan, run

        pattern = r"judge-\d{8}T\d{6}-[a-f0-9]{8}"
        if not re.fullmatch(pattern, args.base_report_id):
            raise ValueError("Invalid base Judge report ID")
        base = _reports(services) / args.base_report_id
        planned = plan(services, base)
        if not args.execute:
            return planned
        if (args.expected_failures != len(planned["failed_ids"])
                or args.max_cost != planned["cost_reservation_max_CNY"]):
            raise ValueError("Execution requires explicitly reviewed exact failure count and cost cap")
        if not services.settings.dashscope_api_key.get_secret_value():
            raise ValueError("Actual recovery execution requires a configured API key")
        resume_id = args.resume_report_id
        if resume_id and not re.fullmatch(pattern, resume_id):
            raise ValueError("Invalid recovery report ID")
        directory = _reports(services) / resume_id if resume_id else _new_run(services, "judge")
        result = run(services, base, directory, resume=bool(resume_id))
        if result["status"] == "incomplete":
            raise RuntimeError("Targeted recovery incomplete; inspect preserved diagnostics")
        return result

    recovery.set_defaults(handler=recovery_handler)
    anchor_recovery = subparsers.add_parser("bonus-judge-recover-anchors")
    anchor_recovery.add_argument("--previous-report-id", required=True)
    anchor_recovery.add_argument("--execute", action="store_true")
    anchor_recovery.add_argument("--expected-failures", type=int)
    anchor_recovery.add_argument("--max-cost", type=float)
    anchor_recovery.add_argument("--resume-report-id")

    def anchor_recovery_handler(args: argparse.Namespace) -> Any:
        from apex_rag.evaluation.judge.anchor_recovery import plan, run

        pattern = r"judge-\d{8}T\d{6}-[a-f0-9]{8}"
        if not re.fullmatch(pattern, args.previous_report_id):
            raise ValueError("Invalid previous Judge report ID")
        previous = _reports(services) / args.previous_report_id
        planned = plan(services, previous)
        if not args.execute:
            return planned
        if (args.expected_failures != len(planned["failed_ids"])
                or args.max_cost != planned["cost_reservation_max_CNY"]):
            raise ValueError("Explicit reviewed exact failure count/cost required")
        if not services.settings.dashscope_api_key.get_secret_value():
            raise ValueError("A configured API key is required")
        resumed = args.resume_report_id
        if resumed and not re.fullmatch(pattern, resumed):
            raise ValueError("Invalid resumed report ID")
        directory = _reports(services) / resumed if resumed else _new_run(services, "judge")
        result = run(services, previous, directory, resume=bool(resumed))
        if result["status"] == "incomplete":
            raise RuntimeError("Anchor-context recovery incomplete; inspect saved evidence")
        return result

    anchor_recovery.set_defaults(handler=anchor_recovery_handler)
    graph_review = subparsers.add_parser("bonus-graph-review")
    graph_review.add_argument("--report-id", required=True)

    def graph_review_handler(args: argparse.Namespace) -> Any:
        if not re.fullmatch(r"graph-\d{8}T\d{6}-[a-f0-9]{8}", args.report_id):
            raise ValueError("Invalid graph report ID.")
        return finalize_graph_review(_reports(services) / args.report_id)

    graph_review.set_defaults(handler=graph_review_handler)


def register_api(router: Any, services: Any) -> None:
    """Expose read-only, non-paid reports; experiment execution stays explicit in CLI."""
    from fastapi import HTTPException

    _install_hooks(services)

    @router.get("/extensions/capabilities")
    def get_capabilities() -> dict[str, Any]:
        return capabilities(services)

    @router.get("/extensions/reports")
    def get_reports() -> dict[str, Any]:
        items = []
        for file in sorted(_reports(services).glob("*/summary.json"), reverse=True)[:100]:
            report = json.loads(file.read_text(encoding="utf-8"))
            items.append(
                {
                    "report_id": file.parent.name,
                    "kind": file.parent.name.split("-")[0],
                    "status": report.get("status"),
                    "metrics": report.get("metrics", {}),
                    "schema_version": "bonus-v1",
                }
            )
        return {"items": items, "total": len(items)}

    @router.get("/extensions/reports/{report_id}")
    def get_report(report_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"(iceberg|graph|judge)-\d{8}T\d{6}-[a-f0-9]{8}", report_id):
            raise HTTPException(404, "Extension report not found.")
        file = _reports(services) / report_id / "summary.json"
        if not file.is_file():
            raise HTTPException(404, "Extension report not found.")
        return {
            "report_id": report_id,
            "kind": report_id.split("-")[0],
            "schema_version": "bonus-v1",
            "data": json.loads(file.read_text(encoding="utf-8")),
        }
