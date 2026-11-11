"""Registration, capability discovery and report reads make no provider calls."""

import argparse
from types import SimpleNamespace

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from apex_rag.extensions.register import register_api, register_cli


def services(tmp_path):
    return SimpleNamespace(
        settings=SimpleNamespace(path=lambda p: tmp_path / p, reports_dir="reports"),
        search=SimpleNamespace(),
    )


def test_cli_registration_without_io(tmp_path):
    svc = services(tmp_path)
    parser = argparse.ArgumentParser()
    register_cli(parser, svc)
    register_cli(parser, svc)
    args = parser.parse_args(["bonus-judge", "--config", "configs/bonus-judge.yaml"])
    assert callable(args.handler) and callable(svc.search.graph_search)
    assert callable(svc.extension_reproduce)


def test_read_only_api_and_traversal_rejection(tmp_path):
    router = APIRouter(prefix="/api/v1")
    register_api(router, services(tmp_path))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/api/v1/extensions/capabilities").json()["graph"]["available"]
        assert client.get("/api/v1/extensions/reports").json() == {"items": [], "total": 0}
        assert client.get("/api/v1/extensions/reports/not-a-report").status_code == 404
        assert client.post("/api/v1/extensions/reports").status_code == 405


def test_graph_review_requires_human_and_records_completeness(tmp_path):
    import json

    import pytest

    from apex_rag.extensions.register import finalize_graph_review

    samples = [
        {"query_id": "c", "mode": m, "status": "completed", "required_documents": ["d"]}
        for m in ("hybrid_rerank", "graph_enhanced")
    ]
    summary = {
        "status": "completed_pending_human",
        "samples": samples,
        "metrics": {
            m: {"expected": 1, "completed": 1} for m in ("hybrid_rerank", "graph_enhanced")
        },
    }
    review = [
        {
            "query_id": "c",
            "mode": m,
            "checklist": ["Fee", "Condition"],
            "supported_and_answered": [True, False],
            "status": "completed",
            "reviewer": "Human test fixture",
            "reviewed_at": "2026-11-09T22:00:00+08:00",
            "notes": "Test fixture only",
        }
        for m in ("hybrid_rerank", "graph_enhanced")
    ]
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    (tmp_path / "human-review.json").write_text(json.dumps(review))
    (tmp_path / "human-annotations.json").write_text(
        json.dumps([{"query_id": "c", "support_doc_ids": ["d"], "human_confirmed": False}])
    )
    with pytest.raises(ValueError):
        finalize_graph_review(tmp_path)
    (tmp_path / "human-annotations.json").write_text(
        json.dumps(
            [
                {
                    "query_id": "c",
                    "support_doc_ids": ["d"],
                    "human_confirmed": True,
                    "human_reviewer": "Human test fixture",
                }
            ]
        )
    )
    result = finalize_graph_review(tmp_path)
    assert (
        result["status"] == "completed"
        and result["metrics"]["graph_enhanced"]["manual_answer_completeness"] == 0.5
    )
