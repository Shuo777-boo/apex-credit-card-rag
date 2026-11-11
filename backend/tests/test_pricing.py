"""Official source snapshot and zero-paid native-usage arithmetic/coverage contracts."""
# ruff: noqa: F811

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from test_database import database_services  # noqa: F401

from apex_rag.core.config import Settings
from apex_rag.evaluation.jobs import EvaluationJobs
from apex_rag.evaluation.pricing import official_pricing, usage_cost
from apex_rag.evaluation.runner import write_json
from apex_rag.main import create_app
from apex_rag.schemas import EvaluationJob, JobEstimate
from apex_rag.services import Services


def record(tokens=100, **updates):
    return {"model": "qwen3-rerank", "status": "completed", "attempts": 1,
            "usage": {"total_tokens": tokens}, **updates}


def cost(records, **updates):
    return usage_cost(records, **{"pricing": official_pricing(), "terminal": True,
                                  "logical_calls": len(records or []), **updates})


def test_official_price_snapshot_rates_and_protocols():
    pricing = official_pricing()
    assert pricing.verified_on == "2026-11-10" and pricing.region == "cn-beijing"
    assert pricing.currency == "CNY" and pricing.invocation == "synchronous_online"
    evidence = Path(__file__).resolve().parents[2] / "reports/core-evidence/pricing-r9"
    for price in pricing.models:
        assert price.input_price == .5 and price.unit_tokens == 1_000_000
        assert price.output_price == 0
        for url, digest in ((price.source_url, price.source_sha256), (price.protocol_url, price.protocol_sha256)):
            raw = evidence / (url.rsplit("/", 1)[-1] + ".html")
            assert hashlib.sha256(raw.read_bytes()).hexdigest() == digest
    assert not official_pricing("singapore").applicable


def test_usage_total_once_not_repeated_by_document_count():
    result = cost([record(1000, documents=100), record(2000, model="text-embedding-v4")])
    assert result.status == "complete"
    assert result.total_usage_cost_cny == .0015
    assert result.known_tokens_by_model == {"qwen3-rerank": 1000, "text-embedding-v4": 2000}
    assert result.known_calls == 2 and result.unknown_calls == 0
    assert result.actual_billed_cny is None


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": 100}, {"total_tokens": True},
                                 {"total_tokens": -1}, {"total_tokens": 1.5}, {"total_tokens": "100"}])
def test_missing_or_non_native_invalid_usage_unknown(usage):
    result = cost([record(usage=usage)])
    assert result.status == "unknown" and result.unknown_calls == 1
    assert result.known_usage_cost_cny is None and result.total_usage_cost_cny is None


@pytest.mark.parametrize("updates", [{"attempts": 2}, {"status": "failed"}, {"attempts": True}, {"attempts": None}])
def test_retries_and_failed_envelopes_retain_known_subset_but_total_unknown(updates):
    result = cost([record(**updates)])
    assert result.status == "partial" and result.known_usage_cost_cny == .00005
    assert result.unknown_calls == 1 and result.total_usage_cost_cny is None


def test_missing_ledger_gaps_pending_nonterminal_and_region_are_never_free():
    for records, options in ((None, {}), ([], {}), ([record()], {"logical_calls": 2}),
        ([record()], {"pending": True}), ([record()], {"terminal": False}),
        ([record()], {"http_attempts": 2}),
        ([record()], {"pricing": official_pricing("singapore")}),
        ([record(model=["untrusted"])], {})):
        assert cost(records, **options).total_usage_cost_cny is None
    assert cost([record(0)]).total_usage_cost_cny == 0  #Actual reported zero tokens.
    assert cost([], zero_call_confirmed=True).total_usage_cost_cny == 0
    assert cost(None, zero_call_confirmed=True, terminal=False).total_usage_cost_cny is None
    legacy = cost([record()], legacy=True)
    assert legacy.valuation_basis == "current_verified_prices_for_legacy_usage"
    assert any("不证明执行当时" in note for note in legacy.notes)


def test_pricing_api_no_database_or_inference(monkeypatch):
    services = Services(Settings())
    def fail(*args, **kwargs):
        raise AssertionError("Pricing cannot touch database/provider")
    monkeypatch.setattr(services.db, "connection", fail)
    monkeypatch.setattr(services.http, "post", fail)
    with TestClient(create_app(services)) as client:
        response = client.get("/api/v1/pricing")
        assert response.status_code == 200 and response.json()["applicable"]
        assert response.json()["models"][0]["input_price"] == .5
        assert "PricingCatalog" in client.get("/openapi.json").json()["components"]["schemas"]


def test_legacy_job_readonly_region_ledger_and_snapshot_handling(database_services):
    services = database_services
    services.db.migrate()
    manager = EvaluationJobs(services)
    identity = str(uuid4())
    run_id = "test-pricing-" + uuid4().hex
    estimate = JobEstimate(configurations=[], total_questions=30, logical_calls_max=30,
        http_attempts_max=90, cost_ceiling_cny=3, conservative_estimate_cny=.1,
        prices_cny_per_million={"qwen3-rerank": .5}, caveats=[])
    data = EvaluationJob(job_id=identity, status="completed", estimate=estimate,
        progress={"total_questions": 30, "total_runs": 1}, runs=[{"run_id": run_id}],
        budget={"logical_calls": 1, "http_attempts": 1}, created_at="test", updated_at="test").model_dump(mode="json")
    #An old r8 record lacks the new fields entirely.
    for field in ("pricing", "usage_cost"):
        data.pop(field)
    with services.db.connection() as conn:
        conn.execute("INSERT INTO evaluation_jobs(job_id,idempotency_key,request_hash,data) VALUES(%s,%s,%s,%s)",
                     (identity, uuid4(), "test", Jsonb(data)))
    services.repository.save_run(run_id, "completed", {"models": {"region": "beijing"}}, {}, {})
    path = services.settings.path(services.settings.reports_dir) / "jobs" / identity / "provider_calls.json"
    write_json(path, [record(1_000_000)])
    original = path.read_bytes()
    enriched = manager.get(identity)
    assert enriched.pricing.applicable and enriched.usage_cost.total_usage_cost_cny == .5
    assert enriched.estimate.pricing is None  #Original estimate retained unchanged.
    assert enriched.usage_cost.valuation_basis == "current_verified_prices_for_legacy_usage"
    assert manager.list().items[0].usage_cost.total_usage_cost_cny == .5
    with services.db.connection() as conn:
        assert conn.execute("SELECT data FROM evaluation_jobs WHERE job_id=%s", (identity,)).fetchone()["data"] == data
    assert path.read_bytes() == original
    #Historical region, not current server settings, governs applicability.
    with services.db.connection() as conn:
        conn.execute("UPDATE evaluation_runs SET config=%s WHERE run_id=%s", (Jsonb({"models": {"region": "singapore"}}), run_id))
    assert manager.get(identity).usage_cost.total_usage_cost_cny is None
    with services.db.connection() as conn:
        conn.execute("UPDATE evaluation_runs SET config=%s WHERE run_id=%s", (Jsonb({"models": {"region": "beijing"}}), run_id))
    for invalid in ("{}", "[null]", "broken-json"):
        path.write_text(invalid, encoding="utf-8")
        assert manager.get(identity).usage_cost.total_usage_cost_cny is None
    path.unlink()
    assert manager.get(identity).usage_cost.total_usage_cost_cny is None
    #New task rate snapshot is preserved even if server settings later change.
    data["pricing"] = official_pricing().model_dump(mode="json")
    write_json(path, [record(100)])
    services.settings.model_region = "singapore"
    assert manager._present(data).usage_cost.valuation_basis == "provider_usage_at_verified_unit_prices"
    assert manager._present(data).usage_cost.total_usage_cost_cny == .00005
