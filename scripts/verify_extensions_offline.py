"""Persist source-graph audit and real Iceberg I/O evidence using explicit test-only vectors.

This command is a mechanism diagnostic, not a real embedding/pgvector benchmark.
"""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from apex_rag.graph.engine import audit, build_graph, expand
from apex_rag.experiments.iceberg.poc import run_poc


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform-tag", default=sys.platform)
    args = parser.parse_args()
    if not args.platform_tag.isalnum():
        parser.error("Platform tag must be alphanumeric.")
    directory = root / "reports/bonus-runs"
    directory.mkdir(parents=True, exist_ok=True)
    docs = json.loads((root / "data" / "kb_documents.json").read_text(encoding="utf-8"))
    graph = build_graph(docs)
    source_audit = audit(graph, docs)
    source_audit.update(
        status="source_audit_only",
        not_retrieval_evidence=True,
        annotation_human_confirmed=False,
        path_example=expand(
            graph,
            ["DOC-0046"],
            "Compare Apex Platinum and Apex Preferred Travel lounge access",
        ),
    )
    (directory / "graph-source-audit.json").write_text(
        json.dumps(source_audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    chunks = [
        {
            "doc_id": f"TEST-{i}",
            "chunk_id": f"c{i}",
            "title": "Synthetic fixture",
            "category": "test",
            "card_tier": "test",
            "effective_year": 2024,
            "text": f"Synthetic test-only fixture {i}",
            "embedding": v,
        }
        for i, v in enumerate(([1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]))
    ]
    repo = SimpleNamespace(
        get_build=lambda: {
            "build_id": "synthetic-test-only",
            "config": {"embedding_fingerprint": "synthetic-test"},
        },
        validate_ready=lambda b: None,
        list_chunks=lambda *a, **k: chunks,
        exact_vector_search=lambda *a: [{"doc_id": f"TEST-{i}"} for i in range(3)],
    )
    services = SimpleNamespace(
        repository=repo,
        embedding=SimpleNamespace(model="synthetic-test-fixture"),
        settings=SimpleNamespace(embedding_fingerprint="synthetic-test"),
    )
    iceberg = run_poc(
        services, directory / ("fixture-iceberg-" + args.platform_tag), top_k=3
    )
    iceberg.update(
        status="mechanism_verified_only",
        uses_synthetic_test_vectors=True,
        pgvector_reference_verified=False,
        real_core_vectors_verified=False,
    )
    (
        directory / ("iceberg-fixture-evidence-" + args.platform_tag + ".json")
    ).write_text(json.dumps(iceberg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "graph_audit_passed": source_audit["passed"],
                "iceberg_time_travel": iceberg["time_travel_verified"],
                "real_core_experiment": "not_verified",
            }
        )
    )
    return 0 if source_audit["passed"] and iceberg["time_travel_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
