"""Actual PostgreSQL/pgvector integration, synthetic vectors ONLY in disposable test schema.

Never use this fixture's ranking as formal evaluation evidence. Set APEX_TEST_DATABASE_URL
to an isolated Docker database. Schema teardown only drops the unique schema this test created.
"""

import os
from uuid import uuid4

import psycopg
import pytest
from pydantic import SecretStr
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from apex_rag.core.config import Settings
from apex_rag.core.errors import ApexError
from apex_rag.ingestion.pipeline import IngestionPipeline
from apex_rag.main import create_app
from apex_rag.providers.aliyun import validate_vectors
from apex_rag.schemas import SearchRequest
from apex_rag.services import Services


class FixtureEmbedding:
    model = "TEST-ONLY-NOT-A-REAL-MODEL"
    dimension = 64

    def __init__(self, fail_after=None):
        self.calls = 0
        self.fail_after = fail_after

    def embed(self, texts, *, input_type="document"):
        import hashlib
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise ApexError("TEST_PROVIDER_FAILURE", "Intentional recoverability test", 502)
        vectors = [[(b + 1) / 256 for b in hashlib.sha512(t.encode()).digest()] for t in texts]
        validate_vectors(vectors, len(texts), 64)
        return vectors


class FixtureReranker:
    model = "TEST-ONLY-NOT-A-REAL-MODEL"

    def rerank(self, query, documents, *, top_n):
        return [(i, 1 / (i + 1)) for i in range(min(top_n, len(documents)))]


@pytest.fixture
def database_services(tmp_path):
    dsn = os.environ.get("APEX_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Real Docker DB unavailable: set APEX_TEST_DATABASE_URL; not an acceptance pass")
    schema = "test_core_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    settings = Settings(database_url=SecretStr(make_conninfo(dsn, options=f"-c search_path={schema},public")), embedding_dimension=64,
                        data_dir=str(tmp_path / "test-only-data"), reports_dir=str(tmp_path / "test-only-reports"))
    services = Services(settings)
    try:
        yield services
    finally:
        services.close()
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.integration
def test_real_pgvector_migration_failure_resume_indexes_filter_and_api(database_services):
    services = database_services
    assert services.db.migrate() == ["001_core", "002_evaluation_jobs"]
    assert services.db.migrate() == []
    failed = FixtureEmbedding(fail_after=3)
    pipeline = IngestionPipeline(services.settings, services.repository, failed)
    with pytest.raises(ApexError, match="recoverability"):
        pipeline.ingest("fixed_window")
    with services.db.connection() as conn:
        build = conn.execute("SELECT * FROM index_builds").fetchone()
        assert build["status"] == "failed"
        assert conn.execute("SELECT count(*) AS n FROM embedding_cache").fetchone()["n"] == 30
    with pytest.raises(ApexError):
        pipeline.activate(build["build_id"])
    good = FixtureEmbedding()
    pipeline.embedding = good
    result = pipeline.ingest("fixed_window")
    assert result["reused"] >= 30 and result["generated"] > 0
    calls = good.calls
    again = pipeline.ingest("fixed_window")
    assert again["generated"] == 0 and good.calls == calls
    pipeline.build_indexes(result["build_id"])
    with pytest.raises(ApexError) as conflict:
        pipeline.build_indexes(result["build_id"], m=24, ef_construction=128)
    assert conflict.value.code == "INDEX_CONFIG_CONFLICT"
    assert pipeline.build_indexes(result["build_id"])["index_reused"] is True
    pipeline.activate(result["build_id"])
    services.repository.validate_ready(services.repository.get_build())
    services.search.embedding = good
    services.search.reranker = FixtureReranker()
    for mode in ["dense", "hybrid", "hybrid_rerank"]:
        response = services.search.search(SearchRequest(query="balance transfer fee", mode=mode,
            top_k=5, card_tier="Apex Platinum"))
        assert response.results
        assert all(r.card_tier == "Apex Platinum" for r in response.results)
        assert len({r.doc_id for r in response.results}) == len(response.results)
        if mode == "dense":
            assert response.trace.sparse == [] and response.timings_ms.sparse is None
        else:
            assert response.trace.sparse and response.trace.fusion
    original_fingerprint = services.settings.workspace_id
    services.settings.workspace_id = "different-workspace-test"
    with pytest.raises(ApexError) as mismatch:
        services.search.search(SearchRequest(query="fee"))
    assert mismatch.value.code == "VECTOR_MISMATCH"
    services.settings.workspace_id = original_fingerprint
    vector = good.embed(["balance transfer fee"], input_type="query")[0]
    exact = services.repository.exact_vector_search(result["build_id"], vector, 5)
    assert len(exact) == 5 and len({r["doc_id"] for r in exact}) == 5
    # Test runner persistence with fixtures only in tmp_path, never official reports.
    from apex_rag.evaluation.runner import evaluate, report, combined_report
    from apex_rag.evaluation.index_benchmark import benchmark_index
    services.embedding = good
    services.reranker = services.search.reranker
    run = evaluate(services, {"mode": "hybrid_rerank"})
    assert run["status"] == "completed"
    detail = services.repository.get_evaluation(run["run_id"])
    assert len(detail.per_query) == 30
    assert report(services, run["run_id"])
    assert combined_report(services, [run])
    benchmark = benchmark_index(services, result["build_id"])
    assert len(benchmark["results"]) == 9
    assert benchmark["results"][0]["default_latency_ms"]["samples"] == 1020
    assert benchmark["results"][0]["forced_index_plan"]
    with TestClient(create_app(services)) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/documents/DOC-0001").json()["effective_year"] == 2024
        assert client.post("/api/v1/search", json={"query": "balance transfer", "mode": "hybrid"}).status_code == 200
