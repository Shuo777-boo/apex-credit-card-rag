"""Zero-paid task guards/state machine; PostgreSQL tests use unique disposable schemas."""
# ruff: noqa: F811

import subprocess
import sys
import threading
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from test_database import FixtureEmbedding, database_services  # noqa: F401

from apex_rag.core.errors import ApexError
from apex_rag.evaluation.jobs import EvaluationJobs, JobHTTP, call_upper
from apex_rag.ingestion.pipeline import IngestionPipeline
from apex_rag.main import create_app
from apex_rag.schemas import EvaluationJob, EvaluationJobRequest, JobEstimate


def estimate():
    return JobEstimate(configurations=[{"build_id": "a" * 24, "strategy": "fixed_window", "mode": "dense"}],
        total_questions=30, logical_calls_max=30, http_attempts_max=90, cost_ceiling_cny=3,
        conservative_estimate_cny=.1, prices_cny_per_million={"text-embedding-v4": .5}, caveats=[])


def test_task_http_reserves_unknown_usage_and_blocks_every_retry(database_services, monkeypatch):
    monkeypatch.setattr("apex_rag.providers.aliyun.time.sleep", lambda _: None)
    calls, guards, snapshots = [], [], []
    def guard():
        guards.append(1)
        if len(guards) == 3:
            raise ApexError("JOB_CANCELLED", "Stop before retry", 409)
    provider = JobHTTP(database_services, estimate(), guard, lambda snapshot, records: snapshots.append(snapshot))
    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(1) or httpx.Response(429)))
    payload = {"model": "text-embedding-v4", "input": {"texts": ["hello"]}}
    with pytest.raises(ApexError) as caught:
        provider.post("/test", payload)
    assert caught.value.code == "JOB_CANCELLED"
    assert len(calls) == 1 and provider.attempts == 1
    assert snapshots[-1]["reserved_upper_cny"] == call_upper(payload, provider.settings.api_max_retries)
    assert provider.calls[-1]["usage"] is None
    assert provider.calls[-1]["error_type"] == "JOB_CANCELLED"
    provider.close()


def test_model_cost_and_logical_limits_before_http(database_services):
    provider = JobHTTP(database_services, estimate(), lambda: None, lambda *_: None)
    provider.reserved_cny = 3
    with pytest.raises(ApexError, match="limit"):
        provider.post("/test", {"model": "text-embedding-v4", "input": {"texts": ["a"]}})
    assert provider.attempts == 0 and provider.calls == []
    with pytest.raises(ApexError, match="allowlisted"):
        call_upper({"model": "qwen3.5-plus", "input": {}}, 2)
    provider.close()


def test_profile_schema_forbids_arbitrary_paths_shell_and_models():
    for extra in ({"shell": "echo bad"}, {"model": "other"}, {"path": "../x"}):
        with pytest.raises(ValueError):
            EvaluationJobRequest(profile="single", idempotency_key=uuid4(), build_id="a" * 24, mode="dense", **extra)
    with pytest.raises(ValueError):
        EvaluationJobRequest(profile="six", idempotency_key=uuid4(), build_ids={"fixed_window": "a" * 24})


def test_pg_jobs_idempotency_cross_connection_lock_cancel_and_crash_recovery(database_services, monkeypatch):
    services = database_services
    assert services.db.migrate() == ["001_core", "002_evaluation_jobs"]
    manager = EvaluationJobs(services)
    monkeypatch.setattr(manager, "estimate", lambda body: estimate())
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    owners = []

    def simulated_worker(data, owner):
        # Tests only: no Services, runner or Provider constructed in this fixture worker.
        owners.append(owner)
        entered.set()
        try:
            release.wait(10)
        finally:
            owner.close()  #Simulated process crash loses advisory ownership, no terminalwrite.
            finished.set()

    monkeypatch.setattr(manager, "_run", simulated_worker)
    body = EvaluationJobRequest(profile="single", idempotency_key=uuid4(), build_id="a" * 24, mode="dense")
    first = manager.submit(body)
    assert entered.wait(3)
    assert first.status == "queued"
    other = EvaluationJobs(services)
    monkeypatch.setattr(other, "estimate", lambda body: estimate())
    assert other.get(first.job_id).status == "queued"  #live lock, not interrupted
    assert manager.submit(body).job_id == first.job_id
    child = subprocess.run([sys.executable, "-c", "import os,psycopg; c=psycopg.connect(os.environ['APEX_TEST_DATABASE_URL']); print(c.execute('SELECT pg_try_advisory_lock(741938008)').fetchone()[0]); c.close()"], capture_output=True, text=True, check=True)
    assert child.stdout.strip() == "False"  #actual cross-process lock, not only Python lock
    conflict = body.model_copy(update={"mode": "hybrid"})
    with pytest.raises(ApexError) as caught:
        manager.submit(conflict)
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(ApexError) as caught:
        other.submit(body.model_copy(update={"idempotency_key": uuid4()}))
    assert caught.value.code == "JOB_LIMIT"
    assert other.cancel(first.job_id).status == "cancelling"
    release.set()
    assert finished.wait(3)
    assert other.get(first.job_id).status == "interrupted"
    assert manager.submit(body).status == "interrupted"  #neverauto replay
    assert other.list().total == 1


def test_pg_orphan_all_active_states_recovered_and_terminal_retained(database_services):
    services = database_services
    services.db.migrate()
    ids = []
    for state in ("queued", "running", "cancelling", "completed"):
        identity = str(uuid4())
        ids.append(identity)
        data = EvaluationJob(job_id=identity, status=state, estimate=estimate(), progress={"total_questions": 30, "total_runs": 1}, created_at="test", updated_at="test").model_dump(mode="json")
        with services.db.connection() as conn:
            conn.execute("INSERT INTO evaluation_jobs(job_id,idempotency_key,request_hash,data) VALUES(%s,%s,%s,%s)", (identity, uuid4(), "test", Jsonb(data)))
    manager = EvaluationJobs(services)
    assert [manager.get(i).status for i in ids] == ["interrupted"] * 3 + ["completed"]
    assert all(manager.get(i).budget.logical_calls == 0 for i in ids)


def test_real_runner_job_completed_persistent_and_cancel_before_second_http(database_services, monkeypatch):
    """Real30-question runner/PG, synthetic64d MockTransport only; not formal evidence."""
    import json
    services = database_services
    services.db.migrate()
    pipe = IngestionPipeline(services.settings, services.repository, FixtureEmbedding())
    build = pipe.ingest("policy_hierarchy")
    pipe.build_indexes(build["build_id"])
    before = services.http.attempts
    original_init = JobHTTP.__init__
    requests = []
    cancel_next = [False]
    manager = EvaluationJobs(services)

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.client.close()
        def handler(request):
            payload = json.loads(request.content)
            requests.append(payload)
            # Reservation + attempt intent must exist DURING provider execution.
            with services.db.connection() as conn:
                row = conn.execute("SELECT data FROM evaluation_jobs ORDER BY created_at DESC LIMIT 1").fetchone()
            assert row["data"]["budget"]["pending_request"]["state"] == "attempt_intent"
            assert row["data"]["budget"]["reserved_upper_cny"] > 0
            if cancel_next[0]:
                cancel_next[0] = False
                manager.cancel(row["data"]["job_id"])
            return httpx.Response(200, json={"output": {"embeddings": [{"text_index": 0, "embedding": [1.] * 64}]}, "usage": {"total_tokens": 10}})
        self.client = httpx.Client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(JobHTTP, "__init__", init)
    body = EvaluationJobRequest(profile="single", idempotency_key=uuid4(), build_id=build["build_id"], mode="dense")
    options = manager.options()
    assert len(options["builds"]) == 1 and options["pricing"]["models"][0]["input_price"] == .5
    proposal = manager.estimate(body)
    assert proposal.logical_calls_max == 30
    assert proposal.quoted_total_cny is None and proposal.quote_status == "unknown_until_provider_usage"
    assert proposal.project_safety_reservation_cny == proposal.conservative_estimate_cny
    #GET/options/estimate cannot trigger model calls, and schema rejects arbitrary configs.
    with TestClient(create_app(services)) as client:
        assert client.get("/api/v1/evaluation-options").status_code == 200
        estimated = client.post("/api/v1/evaluation-jobs/estimate", json=body.model_dump(mode="json"))
        assert estimated.status_code == 200 and estimated.json()["logical_calls_max"] == 30
        assert client.get("/api/v1/evaluation-jobs").json()["total"] == 0
        invalid = {**body.model_dump(mode="json"), "shell": "unsafe"}
        assert client.post("/api/v1/evaluation-jobs", json=invalid).status_code == 422
        assert requests == []
    #TestClient shutdown closed only the pool; recreate for remaining direct-worker checks.
    from apex_rag.storage.db import Database
    services.db = Database(services.settings)
    services.repository.db = services.db
    result = manager.submit(body)
    for thread in manager.threads:
        thread.join(30)
        assert not thread.is_alive()
    completed = manager.get(result.job_id)
    assert completed.status == "completed" and completed.progress.completed_questions == 30
    assert completed.budget.http_attempts == 30 and completed.budget.logical_calls == 30
    assert completed.pricing and completed.usage_cost.status == "complete"
    assert completed.usage_cost.total_usage_cost_cny == .00015
    assert completed.usage_cost.actual_billed_cny is None
    run = services.repository.get_evaluation(completed.runs[0]["run_id"])
    assert len(run.per_query) == 30 and run.status == "completed"
    assert Path(services.settings.reports_dir, "jobs", result.job_id, "provider_calls.json").is_file()
    assert services.http.attempts == before  #interactive provider is isolated
    cancel_next[0] = True
    cancelled = manager.submit(body.model_copy(update={"idempotency_key": uuid4()}))
    for thread in manager.threads:
        thread.join(15)
        assert not thread.is_alive()
    saved = manager.get(cancelled.job_id)
    assert saved.status == "cancelled" and saved.progress.completed_questions == 1
    assert saved.budget.http_attempts == 1
    assert len(requests) == 31
    assert not saved.budget.pending_request
    assert services.repository.get_evaluation(saved.runs[0]["run_id"]).status == "cancelled"
    manager.close()


def test_thread_start_failure_releases_lock_and_marks_failed(database_services, monkeypatch):
    services = database_services
    services.db.migrate()
    manager = EvaluationJobs(services)
    monkeypatch.setattr(manager, "estimate", lambda _: estimate())
    with services.db.connection() as conn:
        conn.execute("SELECT 1")
    def fail(*args, **kwargs):
        raise RuntimeError("fixturefailure")
    monkeypatch.setattr(threading.Thread, "start", fail)
    body = EvaluationJobRequest(profile="single", idempotency_key=uuid4(), build_id="a" * 24, mode="dense")
    with pytest.raises(ApexError) as caught:
        manager.submit(body)
    assert caught.value.code == "JOB_START_FAILED"
    assert manager.list().items[0].status == "failed"
    assert manager.threads == []


def test_preparation_failure_terminal_and_lock_released(database_services, monkeypatch):
    services = database_services
    services.db.migrate()
    manager = EvaluationJobs(services)
    monkeypatch.setattr(manager, "estimate", lambda _: estimate())
    original = Path.mkdir
    def fail(self, *args, **kwargs):
        if "jobs" in self.parts:
            raise PermissionError("fixturepermission")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", fail)
    #Disk failure still leaves terminal DB evidence and releases lock.
    created = manager.submit(EvaluationJobRequest(profile="single", idempotency_key=uuid4(), build_id="a" * 24, mode="dense"))
    manager.threads[-1].join(5)
    assert not manager.threads[-1].is_alive()
    assert manager.get(created.job_id).status == "failed"
    assert manager.get(created.job_id).error["code"] == "JOB_ARTIFACT_WRITE_FAILED"


def test_shutdown_marks_running_job_interrupted_without_paid_replay(database_services, monkeypatch):
    import time
    services = database_services
    services.db.migrate()
    manager = EvaluationJobs(services)
    monkeypatch.setattr(manager, "estimate", lambda _: estimate())
    entered = threading.Event()
    def worker(data, owner):
        entered.set()
        try:
            manager.stop.wait(3)
        finally:
            owner.close()
    monkeypatch.setattr(manager, "_run", worker)
    created = manager.submit(EvaluationJobRequest(profile="single", idempotency_key=uuid4(), build_id="a" * 24, mode="dense"))
    assert entered.wait(2)
    at = time.monotonic()
    manager.close()
    assert time.monotonic() - at < 6
    assert manager.get(created.job_id).status == "interrupted"
    with pytest.raises(ApexError) as caught:
        manager.submit(EvaluationJobRequest(profile="single", idempotency_key=uuid4(), build_id="a" * 24, mode="dense"))
    assert caught.value.code == "JOB_WORKER_UNAVAILABLE"
