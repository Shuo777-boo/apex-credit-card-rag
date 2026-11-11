"""Real local Iceberg manifests and time travel with clearly synthetic test-only vectors."""

from types import SimpleNamespace

import pytest

from apex_rag.experiments.iceberg.poc import arrow_rows, cosine_topk, run_poc


def test_real_snapshots_and_manifest_scan(tmp_path):
    chunks = [
        {
            "doc_id": f"TEST-{i}",
            "chunk_id": f"c{i}",
            "title": "test fixture",
            "category": "test",
            "card_tier": "test",
            "effective_year": 2024,
            "text": f"synthetic fixture {i}",
            "embedding": v,
        }
        for i, v in enumerate(([1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]))
    ]
    # This adapter is only for exercising Iceberg APIs. It is never reported as pgvector validation.
    repo = SimpleNamespace(
        get_build=lambda: {"build_id": "test-only", "config": {"embedding_fingerprint": "test"}},
        validate_ready=lambda b: None,
        list_chunks=lambda *a, **k: chunks,
        exact_vector_search=lambda *a: [
            {"doc_id": "TEST-0"},
            {"doc_id": "TEST-1"},
            {"doc_id": "TEST-2"},
        ],
    )
    services = SimpleNamespace(
        repository=repo,
        embedding=SimpleNamespace(model="synthetic-test-fixture"),
        settings=SimpleNamespace(embedding_fingerprint="test"),
    )
    run_dir = tmp_path / "中文 仓库"
    result = run_poc(services, run_dir, top_k=3)
    assert list((run_dir / "warehouse").rglob("*.metadata.json"))
    # PyIceberg overwrite may commit a delete snapshot followed by an append snapshot.
    assert result["time_travel_verified"] and len(result["snapshots"]) >= 2
    assert all(s["manifest_list"] for s in result["snapshots"])
    assert result["snapshots"][0]["manifests"] and result["snapshots"][-1]["manifests"]
    assert result["same_doc_order"]


def test_cosine_guards_and_dedup():
    chunks = [
        {
            "doc_id": "a",
            "chunk_id": str(i),
            "title": "t",
            "category": "c",
            "card_tier": "p",
            "effective_year": 2024,
            "text": "fixture",
            "embedding": [1.0, float(i)],
        }
        for i in range(2)
    ]
    table = arrow_rows(chunks, {"build_id": "test"}, "fixture")
    assert len(cosine_topk(table, [1.0, 0.0])["results"]) == 1
    with pytest.raises(ValueError):
        cosine_topk(table, [0.0, 0.0])
    with pytest.raises(ValueError):
        cosine_topk(table, [1.0])
