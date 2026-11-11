"""Explicitly submitted, bounded evaluation jobs using the sole official runner.

One PostgreSQL session advisory lock spans admission through worker completion. No queue
polling daemon and no automatic paid replay. Losing that connection marks jobs interrupted
on the next job API access; cancellation stops future questions and HTTP attempts.
"""

import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

from apex_rag.core.errors import ApexError
from apex_rag.evaluation.pricing import PRICE_PER_MILLION, official_pricing, usage_cost
from apex_rag.evaluation.runner import evaluate, report, write_json
from apex_rag.ingestion.data import load_official
from apex_rag.providers.aliyun import AliyunEmbedding, AliyunLLM, AliyunReranker, DashScopeHTTP
from apex_rag.schemas import EvaluationJob, EvaluationJobList, EvaluationJobRequest, JobEstimate
from apex_rag.services import Services

LOCK = 741938008
ACTIVE = {"queued", "running", "cancelling"}
PRICES = PRICE_PER_MILLION


def now() -> str:
    return datetime.now(UTC).isoformat()


def call_upper(payload: dict[str, Any], retries: int) -> float:
    """Reserve all potential attempts, query repeated per doc, UTF8 bytes plus overhead.

    This is a project safety budget, not an official quote or tokenizer. Output/chat is
    prohibited in jobs; only the two frozen retrieval models have verified prices here.
    """
    model = payload.get("model")
    if model not in PRICES:
        raise ApexError("MODEL_BUDGET_EXCEEDED", "Job model is not allowlisted", 429)
    inp = payload["input"]
    if "documents" in inp:
        tokens = sum(len(d.encode()) + len(inp["query"].encode()) for d in inp["documents"])
    else:
        tokens = sum(len(t.encode()) for t in inp["texts"])
    return (tokens + 2048) * (retries + 1) * PRICES[model] / 1_000_000


class JobHTTP(DashScopeHTTP):
    def __init__(self, services: Services, estimate: JobEstimate, guard: Any, persist: Any) -> None:
        super().__init__(services.settings)
        self.estimate, self.guard, self.persist = estimate, guard, persist
        self.reserved_cny = 0.0
        self.pending: dict[str, Any] | None = None
        self.before_attempt = guard

    def snapshot(self) -> dict[str, Any]:
        return {"logical_calls": len(self.calls), "http_attempts": self.attempts,
                "reserved_upper_cny": self.reserved_cny, "actual_billed_cny": None,
                "logical_limit": self.estimate.logical_calls_max, "http_limit": self.estimate.http_attempts_max,
                "cost_limit_cny": self.estimate.cost_ceiling_cny, "pending_request": self.pending}

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.guard()
        reservation = call_upper(payload, self.settings.api_max_retries)
        if len(self.calls) >= self.estimate.logical_calls_max or self.reserved_cny + reservation > self.estimate.cost_ceiling_cny:
            raise ApexError("MODEL_BUDGET_EXCEEDED", "Task logical or cost limit reached", 429)
        self.reserved_cny += reservation
        self.pending = {"model": payload["model"], "logical_sequence": len(self.calls) + 1,
                        "state": "reserved_before_http", "reserved_cny": reservation,
                        "max_attempts": self.settings.api_max_retries + 1,
                        "note": "If worker stops before outcome persistence, execution/billing remains unknown"}
        self.persist(self.snapshot(), self.calls)
        def before() -> None:
            self.guard()
            assert self.pending
            self.pending.update(state="attempt_intent", http_attempt_sequence=self.attempts + 1)
            self.persist(self.snapshot(), self.calls)
        self.before_attempt = before
        try:
            return super().post(path, payload)
        finally:
            self.pending = None
            self.persist(self.snapshot(), self.calls)


class EvaluationJobs:
    def __init__(self, services: Services) -> None:
        self.services = services
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []

    def recover(self) -> None:
        """No worker lock means no valid execution owner; never resume paid work."""
        with self.services.db.connection() as conn:
            acquired = conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS acquired", (LOCK,)).fetchone()
            assert acquired
            if acquired["acquired"]:
                rows = conn.execute("SELECT job_id,data FROM evaluation_jobs WHERE data->>'status' IN ('queued','running','cancelling') FOR UPDATE").fetchall()
                for row in rows:
                    data = row["data"]
                    data.update(status="interrupted", updated_at=now(), error={"code": "JOB_INTERRUPTED", "message": "Worker ownership lost; not replayed. Submit a new explicit task to retry."})
                    if data.get("budget", {}).get("pending_request"):
                        data["budget"]["pending_request"]["state"] = "outcome_unknown_after_interruption"
                    for run in data["runs"]:
                        conn.execute("UPDATE evaluation_runs SET status='interrupted' WHERE run_id=%s AND status='running'", (run["run_id"],))
                    conn.execute("UPDATE evaluation_jobs SET data=%s,updated_at=now() WHERE job_id=%s", (Jsonb(data), row["job_id"]))

    def options(self) -> dict[str, Any]:
        builds = []
        with self.services.db.connection() as conn:
            rows = conn.execute("SELECT build_id FROM index_builds WHERE status='ready' ORDER BY created_at DESC").fetchall()
        for row in rows:
            build = self.services.repository.get_build(row["build_id"])
            if build["config"]["embedding_fingerprint"] != self.services.settings.embedding_fingerprint:
                continue
            try:
                counts = self.services.repository.validate_ready(build)
            except ApexError:
                continue
            builds.append({"build_id": build["build_id"], "strategy": build["config"]["strategy"],
                           "documents": counts["documents"], "chunks": counts["chunks"], "models": self.services.settings.public_models()})
        return {"builds": builds, "profiles": ["single", "six"], "modes": ["dense", "hybrid", "hybrid_rerank"],
                "candidate_depth": 100, "rerank_depth": 100, "top_k": 20, "questions_per_run": 30,
                "limits": {"concurrent_jobs": 1, "single_http_attempts": 180, "six_http_attempts": 720,
                           "single_cost_cny": 3, "six_cost_cny": 12, "single_timeout_seconds": 900, "six_timeout_seconds": 3600},
                "prices_cny_per_million": PRICES, "pricing": official_pricing(self.services.settings.model_region).model_dump(mode="json"),
                "conversation": {"max_messages": 6, "max_message_chars": 4000,
                    "max_total_chars": 12000, "context_mode": "bounded_history_rewrite", "history_is_policy_evidence": False}}

    def estimate(self, body: EvaluationJobRequest) -> JobEstimate:
        settings = self.services.settings
        if settings.model_region != "beijing":
            raise ApexError("JOB_CONFIG_UNSUPPORTED", "Only the verified Beijing job tariffs are enabled", 409)
        if settings.api_max_retries > 2:
            raise ApexError("JOB_CONFIG_UNSUPPORTED", "Task retry maximum is2", 409)
        if settings.embedding_model != "text-embedding-v4" or settings.rerank_model != "qwen3-rerank":
            raise ApexError("JOB_CONFIG_UNSUPPORTED", "Task tariffs require frozen core models", 409)
        choices: list[tuple[str, str]]
        if body.profile == "single":
            assert body.build_id and body.mode
            choices = [(body.build_id, body.mode)]
        else:
            assert body.build_ids
            choices = [(body.build_ids[strategy], mode) for strategy in ("fixed_window", "policy_hierarchy") for mode in ("dense", "hybrid", "hybrid_rerank")]  # type: ignore[index]
        _, questions, _ = load_official(settings.path(settings.data_dir))
        configurations = []
        logical, estimated = 0, 0.0
        cache: dict[str, Any] = {}
        for build_id, mode in choices:
            build = self.services.repository.get_build(build_id)
            self.services.repository.validate_ready(build)
            if build["config"]["embedding_fingerprint"] != settings.embedding_fingerprint:
                raise ApexError("VECTOR_MISMATCH", "Build does not match current query embedding", 409)
            strategy = build["config"]["strategy"]
            if body.profile == "six" and (strategy not in ("fixed_window", "policy_hierarchy") or body.build_ids.get(strategy) != build_id):  # type: ignore[union-attr]
                raise ApexError("JOB_CONFIG_UNSUPPORTED", "Both strategy builds must match their selected keys", 409)
            if build_id not in cache:
                cache[build_id] = self.services.repository.list_chunks(build_id)
            # Before ranking is known, longest100 contexts bound any rerank100 payload.
            longest = sorted([c["context_text"] for c in cache[build_id]], key=lambda x: len(x.encode()), reverse=True)[:100]
            configurations.append({"build_id": build_id, "strategy": strategy, "mode": mode})
            for q in questions:
                estimated += call_upper({"model": settings.embedding_model, "input": {"texts": [q.query]}}, settings.api_max_retries)
                logical += 1
                if mode == "hybrid_rerank":
                    estimated += call_upper({"model": settings.rerank_model, "input": {"query": q.query, "documents": longest}}, settings.api_max_retries)
                    logical += 1
        ceiling = 3.0 if body.profile == "single" else 12.0
        if estimated > ceiling:
            raise ApexError("MODEL_BUDGET_EXCEEDED", "Project safety reservation exceeds server budget", 409)
        return JobEstimate(configurations=configurations, total_questions=30 * len(configurations), logical_calls_max=logical,
            http_attempts_max=logical * (settings.api_max_retries + 1), cost_ceiling_cny=ceiling,
            conservative_estimate_cny=estimated, retries_max=settings.api_max_retries, prices_cny_per_million=PRICES,
            project_safety_reservation_cny=estimated, pricing=official_pricing(settings.model_region),
            caveats=["官方单价见pricing及来源；准确Token与候选未知，任务总费用未知，quoted_total_cny=null。",
                     "UTF8字节+2048余量及所有重试仅用于项目安全预算，不是官方报价或准确Token计数。",
                     "Only explicit submission starts paid calls; actual provider usage is not an invoice",
                     "No LLM answers/Judge; fixed official30questions per configuration; no query cache", "Cancellation cannot undo an in-flight request; no next request after observed cancellation",
                     "30-question P99 is exploratory; no automatic100ms acceptance"])

    def _present(self, data: dict[str, Any]) -> EvaluationJob:
        """Enrich a copy only; never backfill current prices into historical evidence."""
        job = EvaluationJob.model_validate(data)
        legacy = job.pricing is None
        if legacy:
            # The running server's region does not establish a historical job's region.
            run_ids = [r["run_id"] for r in job.runs if r.get("run_id")]
            configs = []
            if run_ids:
                with self.services.db.connection() as conn:
                    configs = conn.execute("SELECT config FROM evaluation_runs WHERE run_id=ANY(%s)", (run_ids,)).fetchall()
            verified = bool(run_ids) and len(configs) == len(run_ids) and all(
                r["config"].get("models", {}).get("region") == "beijing" for r in configs)
            job.pricing = official_pricing("beijing" if verified else "unknown")
        assert job.pricing
        records = None
        try:
            # Only canonical UUID-derived paths below the configured job root are read.
            identity = str(UUID(job.job_id))
            root = (self.services.settings.path(self.services.settings.reports_dir) / "jobs").resolve()
            path = (root / identity / "provider_calls.json").resolve()
            if path.is_relative_to(root):
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, list) and all(isinstance(r, dict) for r in loaded):
                    records = loaded
        except (ValueError, OSError):
            pass  #Missing/unreadable/malformed evidence is unknown, never zero usage.
        terminal = job.status not in ACTIVE
        diagnostic_zero = (job.estimate.total_questions == 0 and job.estimate.logical_calls_max == 0
            and job.estimate.http_attempts_max == 0 and any("DIAGNOSTIC_ORPHAN_FIXTURE" in c for c in job.estimate.caveats))
        zero_confirmed = terminal and (diagnostic_zero or (job.error or {}).get("code") == "JOB_START_FAILED") and job.budget.http_attempts == 0
        job.usage_cost = usage_cost(records, pricing=job.pricing, terminal=terminal,
            logical_calls=job.budget.logical_calls, pending=bool(job.budget.pending_request),
            legacy=legacy, zero_call_confirmed=zero_confirmed, http_attempts=job.budget.http_attempts)
        return job

    def get(self, identity: str) -> EvaluationJob:
        self.recover()
        with self.services.db.connection() as conn:
            row = conn.execute("SELECT data FROM evaluation_jobs WHERE job_id::text=%s", (identity,)).fetchone()
        if not row:
            raise ApexError("NOT_FOUND", "Evaluation job not found", 404)
        return self._present(row["data"])

    def list(self) -> EvaluationJobList:
        self.recover()
        with self.services.db.connection() as conn:
            rows = conn.execute("SELECT data FROM evaluation_jobs ORDER BY created_at DESC LIMIT 100").fetchall()
            count = conn.execute("SELECT count(*) AS n FROM evaluation_jobs").fetchone()
            assert count
            total = count["n"]
        return EvaluationJobList(items=[self._present(r["data"]) for r in rows], total=total)

    def cancel(self, identity: str) -> EvaluationJob:
        self.get(identity)
        with self.services.db.connection() as conn:
            conn.execute("UPDATE evaluation_jobs SET cancel_requested=true,data=jsonb_set(data,'{status}','\"cancelling\"'::jsonb),updated_at=now() WHERE job_id::text=%s AND data->>'status' IN ('queued','running','cancelling')", (identity,))
        return self.get(identity)

    def submit(self, body: EvaluationJobRequest) -> EvaluationJob:
        if self.stop.is_set():
            raise ApexError("JOB_WORKER_UNAVAILABLE", "Server is shutting down", 503)
        digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        # Existing submission must return original evidence even if build is later unavailable.
        self.recover()
        with self.services.db.connection() as db:
            existing = db.execute("SELECT request_hash,data FROM evaluation_jobs WHERE idempotency_key=%s", (body.idempotency_key,)).fetchone()
        if existing:
            if existing["request_hash"] != digest:
                raise ApexError("IDEMPOTENCY_CONFLICT", "Key was used for another task configuration", 409)
            return self._present(existing["data"])
        estimate = self.estimate(body)
        dsn = self.services.settings.database_url.get_secret_value()
        if conninfo_to_dict(dsn).get("host") == "localhost":
            dsn = make_conninfo(dsn, host="127.0.0.1")
        owner = psycopg.connect(dsn, connect_timeout=5, autocommit=True)
        try:
            acquired = owner.execute("SELECT pg_try_advisory_lock(%s)", (LOCK,)).fetchone()
            assert acquired
            if not acquired[0]:
                # A same-key concurrent race may have finished admission meanwhile.
                with self.services.db.connection() as db:
                    existing = db.execute("SELECT request_hash,data FROM evaluation_jobs WHERE idempotency_key=%s", (body.idempotency_key,)).fetchone()
                if existing and existing["request_hash"] == digest:
                    owner.close()
                    return self._present(existing["data"])
                raise ApexError("JOB_LIMIT", "Only one evaluation job may execute globally", 409)
            # Lock reservation precedes insert and remains held by this same session in thread.
            identity = str(uuid4())
            data = EvaluationJob(job_id=identity, status="queued", estimate=estimate, pricing=estimate.pricing,
                progress={"total_questions": estimate.total_questions, "total_runs": len(estimate.configurations)},  # type: ignore[arg-type]
                created_at=now(), updated_at=now()).model_dump(mode="json")
            initial = EvaluationJob.model_validate(data)
            with self.services.db.connection() as db:
                existing = db.execute("SELECT request_hash,data FROM evaluation_jobs WHERE idempotency_key=%s", (body.idempotency_key,)).fetchone()
                if existing:
                    owner.close()
                    if existing["request_hash"] != digest:
                        raise ApexError("IDEMPOTENCY_CONFLICT", "Key configuration conflict", 409)
                    return self._present(existing["data"])
                db.execute("INSERT INTO evaluation_jobs(job_id,idempotency_key,request_hash,data) VALUES(%s,%s,%s,%s)", (identity, body.idempotency_key, digest, Jsonb(data)))
            worker = threading.Thread(target=self._run, args=(data, owner), daemon=True, name=f"evaluation-{identity}")
            self.threads.append(worker)
            try:
                worker.start()
            except Exception:
                self.threads.remove(worker)
                data.update(status="failed", updated_at=now(), error={"code": "JOB_START_FAILED", "message": "Worker did not start; no model call"})
                with self.services.db.connection() as db:
                    db.execute("UPDATE evaluation_jobs SET data=%s,updated_at=now() WHERE job_id=%s", (Jsonb(data), identity))
                raise ApexError("JOB_START_FAILED", "Evaluation worker did not start", 503) from None
            return initial
        except BaseException:
            owner.close()
            raise

    def _run(self, data: dict[str, Any], owner: Any) -> None:
        task: Services | None = None
        deadline = time.monotonic() + (900 if len(data["estimate"]["configurations"]) == 1 else 3600)
        root = self.services.settings.path(self.services.settings.reports_dir) / "jobs" / data["job_id"]
        identity = data["job_id"]

        def persist() -> None:
            data["updated_at"] = now()
            if task and data.get("pricing"):
                data["usage_cost"] = usage_cost(task.http.calls, pricing=estimate.pricing or official_pricing("unknown"),
                    terminal=data["status"] not in ACTIVE, logical_calls=data["budget"]["logical_calls"],
                    pending=bool(data["budget"].get("pending_request")), http_attempts=data["budget"]["http_attempts"]).model_dump(mode="json")
            with self.services.db.connection() as conn:
                # A separately persisted cancellation/shutdown must not be overwritten by progress.
                row = conn.execute("SELECT cancel_requested,data->>'status' AS status FROM evaluation_jobs WHERE job_id=%s FOR UPDATE", (identity,)).fetchone()
                assert row
                if row["status"] == "interrupted":
                    data["status"] = "interrupted"
                elif row["cancel_requested"] and data["status"] in ACTIVE:
                    data["status"] = "cancelling"
                if data["status"] in {"interrupted", "cancelled", "failed"}:
                    for run in data["runs"]:
                        conn.execute("UPDATE evaluation_runs SET status=%s WHERE run_id=%s AND status='running'", (data["status"], run["run_id"]))
                conn.execute("UPDATE evaluation_jobs SET data=%s,updated_at=now() WHERE job_id=%s", (Jsonb(data), identity))
            write_json(root / "job.json", data)

        def cancelled() -> bool:
            with self.services.db.connection() as conn:
                row = conn.execute("SELECT cancel_requested FROM evaluation_jobs WHERE job_id=%s", (identity,)).fetchone()
                assert row
            return self.stop.is_set() or bool(row["cancel_requested"])

        def guard() -> None:
            # Probe lock-owning session so connection loss cannot allow an unowned next call.
            owner.execute("SELECT 1")
            if cancelled():
                raise ApexError("JOB_CANCELLED", "Task cancelled; no further model request", 409)
            if time.monotonic() >= deadline:
                raise ApexError("JOB_TIMEOUT", "Task deadline reached", 409)

        def calls(budget: dict[str, Any], records: Any) -> None:
            data["budget"] = budget
            write_json(root / "provider_calls.json", records)
            persist()

        try:
            root.mkdir(parents=True, exist_ok=True)
            estimate = JobEstimate.model_validate(data["estimate"])
            settings = self.services.settings.model_copy(update={"api_max_requests": estimate.http_attempts_max,
                                                                 "pool_max_size": 2, "pool_min_size": 1})
            task = Services(settings)
            task.http.close()
            http = JobHTTP(task, estimate, guard, calls)
            task.http = http
            task.embedding, task.reranker, task.llm = AliyunEmbedding(http), AliyunReranker(http), AliyunLLM(http)
            data["status"] = "running"
            persist()
            for config in estimate.configurations:
                guard()
                prior = data["progress"]["completed_questions"]

                def progress(event: dict[str, Any], config: Any = config, prior: int = prior) -> None:
                    if not any(r["run_id"] == event["run_id"] for r in data["runs"]):
                        data["runs"].append({"run_id": event["run_id"], "status": "running", **config})
                    data["progress"].update(completed_questions=prior + event["completed_questions"], current_query_id=event["current_query_id"])
                    persist()

                result = evaluate(task, {**config, "candidate_depth": 100, "rerank_depth": 100, "job_id": identity},
                                  should_stop=cancelled, on_progress=progress)
                data["runs"][-1].update(result)
                report(task, result["run_id"])
                data["progress"]["completed_runs"] += 1
                persist()
                if result["status"] != "completed":
                    data["status"] = "cancelled" if cancelled() else "failed"
                    data["error"] = {"code": "JOB_CANCELLED" if cancelled() else "EVALUATION_FAILED", "message": "Partial run retained; later configurations were not started"}
                    break
            else:
                data["status"] = "cancelled" if cancelled() else "completed"
        except Exception as exc:
            data["status"] = "cancelled" if isinstance(exc, ApexError) and exc.code == "JOB_CANCELLED" else "failed"
            data["error"] = {"code": exc.code if isinstance(exc, ApexError) else "JOB_EXECUTION_FAILED",
                             "message": exc.message if isinstance(exc, ApexError) else "Task failed; partial evidence retained"}
            if task:
                for run in data["runs"]:
                    with task.db.connection() as conn:
                        conn.execute("UPDATE evaluation_runs SET status=%s WHERE run_id=%s AND status='running'", (data["status"], run["run_id"]))
        finally:
            try:
                try:
                    persist()
                except OSError:
                    # DB remains authoritative if artifact storage is unavailable.
                    data.update(status="failed", updated_at=now(), error={"code": "JOB_ARTIFACT_WRITE_FAILED", "message": "Task artifact storage unavailable; persisted database evidence retained"})
                    with self.services.db.connection() as conn:
                        conn.execute("UPDATE evaluation_jobs SET data=%s,updated_at=now() WHERE job_id=%s", (Jsonb(data), identity))
            finally:
                if task:
                    task.close()
                owner.close()

    def close(self) -> None:
        """Bounded shutdown: mark interruption, no paid replay; in-flight HTTP may finish.

        Daemon worker can outlive the5s join; process termination may lose last response.
        Persist this fact before return, never promise cancellation of a provider request.
        """
        self.stop.set()
        with self.services.db.connection() as conn:
            # Only this process's threads; another server's job must retain its owner.
            for thread in self.threads:
                identity = thread.name.removeprefix("evaluation-")
                conn.execute("UPDATE evaluation_jobs SET cancel_requested=true,data=jsonb_set(data,'{status}','\"interrupted\"'::jsonb),updated_at=now() WHERE job_id::text=%s AND data->>'status' IN ('queued','running','cancelling')", (identity,))
                conn.execute("UPDATE evaluation_runs SET status='interrupted' WHERE status='running' AND config->>'job_id'=%s", (identity,))
        until = time.monotonic() + 5
        for thread in self.threads:
            thread.join(timeout=max(0, until - time.monotonic()))
