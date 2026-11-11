"""Bounded, opt-in real model exploration; never changes serving settings/builds.

Run from the project root with the Conda Python. Preflight, frozen inputs and official
price snapshots must already exist. All attempts share one process budget/client.
"""

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path
from typing import Any

import numpy as np
from psycopg import sql

from apex_rag.core.config import Settings
from apex_rag.core.errors import ApexError
from apex_rag.evaluation.metrics import quantiles, retrieval_metrics
from apex_rag.providers.aliyun import AliyunReranker, DashScopeHTTP, validate_vectors
from apex_rag.retrieval.service import aggregate, rrf, trace
from apex_rag.storage.db import Database
from apex_rag.storage.repository import Repository

RATES = {"text-embedding-v4": .5, "text-embedding-v2": .7,
         "qwen3-rerank": .5, "gte-rerank-v2": .8}


def write(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def cost_bound(payload: dict[str, Any]) -> float:
    """Conservative UTF8-byte token estimate plus overhead, including one retry.

    Rerank repeats the query for each document, as the official billing formula does.
    This is a spending guard estimate, not an invoice or tokenizer measurement.
    """
    inp = payload["input"]
    if "documents" in inp:
        count = sum(len(x.encode("utf-8")) + len(inp["query"].encode("utf-8"))
                    for x in inp["documents"])
    else:
        count = sum(len(x.encode("utf-8")) for x in inp["texts"])
    return (count + 2048) * 2 * RATES[payload["model"]] / 1_000_000


class BudgetHTTP(DashScopeHTTP):
    """Reserve worst-case spend before HTTP; flush success/failure records every call."""

    def __init__(self, settings: Settings, output: Path, client: Any = None) -> None:
        super().__init__(settings, client)
        self.output = output
        self.reserved_cny = 0.0
        self.tag: dict[str, Any] = {}

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        reserve = cost_bound(payload)
        if len(self.calls) >= 100 or self.reserved_cny + reserve > 10:
            raise ApexError("MODEL_BUDGET_EXCEEDED", "Diagnostic hard budget reached", 429)
        self.reserved_cny += reserve
        before = len(self.calls)
        try:
            return super().post(path, payload)
        finally:
            if len(self.calls) > before:
                self.calls[-1].update(self.tag, sequence=len(self.calls),
                    reserved_cny=reserve, payload_sha256=hashlib.sha256(
                        json.dumps(payload, sort_keys=True).encode()).hexdigest())
            write(self.output / "provider-calls.json", {
                "logical_calls": len(self.calls), "http_attempts": self.attempts,
                "reserved_upper_cny": self.reserved_cny, "actual_billed_cny": None,
                "caps": {"logical": 100, "http": 200, "cny": 10, "retry": 1},
                "calls": self.calls})


def embed(http: BudgetHTTP, model: str, texts: list[str], kind: str) -> list[list[float]]:
    parameters: dict[str, Any] = {"text_type": kind}
    dim = 1536 if model == "text-embedding-v2" else 1024
    if model == "text-embedding-v4":
        parameters.update(dimension=dim, output_type="dense")
    result = http.post("/api/v1/services/embeddings/text-embedding/text-embedding",
                       {"model": model, "input": {"texts": texts}, "parameters": parameters})
    items = result["output"]["embeddings"]
    if sorted(x["text_index"] for x in items) != list(range(len(texts))):
        raise ValueError("Embedding indices differ from exact inputs")
    vectors = [[float(v) for v in x["embedding"]] for x in sorted(items, key=lambda x: x["text_index"])]
    validate_vectors(vectors, len(texts), dim)
    return vectors


def isolated_table(db: Database, chunks: list[dict[str, Any]], vectors: list[list[float]]) -> str:
    """Session-local pgvector1536/HNSW/GIN; no registry or active-build mutation."""
    validate_vectors(vectors, len(chunks), 1536)
    name = "chunks_" + hashlib.sha256(b"model-speed-v2-isolated-20261109").hexdigest()[:24]
    table = sql.Identifier(name)
    with db.connection() as conn:
        conn.execute(sql.SQL("CREATE TEMP TABLE {} (chunk_id text PRIMARY KEY,doc_id text,text text,context_text text,parent_id text,start_char int,end_char int,embedding vector(1536),search_vector tsvector GENERATED ALWAYS AS (to_tsvector('english',context_text)) STORED)").format(table))
        with conn.cursor() as cur:
            cur.executemany(sql.SQL("INSERT INTO {}(chunk_id,doc_id,text,context_text,parent_id,start_char,end_char,embedding) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)").format(table),
                [(c["chunk_id"], c["doc_id"], c["text"], c["context_text"], c["parent_id"], c["start_char"], c["end_char"], np.asarray(v, dtype=np.float32)) for c, v in zip(chunks, vectors, strict=True)])
        conn.execute(sql.SQL("CREATE INDEX ON {} USING hnsw(embedding vector_cosine_ops) WITH(m=16,ef_construction=64)").format(table))
        conn.execute(sql.SQL("CREATE INDEX ON {} USING gin(search_vector)").format(table))
        conn.execute(sql.SQL("ANALYZE {}").format(table))
    return name


def main(output: Path) -> None:
    if (output / "provider-calls.json").exists():
        raise ValueError("Existing paid audit: never silently rerun or reset this budget")
    manifest = json.loads((output / "preflight.json").read_text(encoding="utf-8"))
    chunks = json.loads((output / "source-chunks.json").read_text(encoding="utf-8"))
    cases = manifest["cases"]
    settings = Settings(api_max_retries=1, api_max_requests=200, pool_min_size=1, pool_max_size=1)
    if settings.model_region != "beijing" or settings.dashscope_base.split("//")[-1] != manifest["endpoint_host"]:
        raise ValueError("Preflight region/endpoint changed")
    db = Database(settings)
    repo = Repository(db)
    http = BudgetHTTP(settings, output)
    reranker = AliyunReranker(http)
    lookup = {c["chunk_id"]: c for c in chunks}
    stage: list[dict[str, Any]] = []
    full: list[dict[str, Any]] = []
    query_vectors: list[dict[str, Any]] = []
    available = {m: True for m in RATES}
    active_before = repo.get_build()["build_id"]
    base = repo.get_build(manifest["build_id"])
    status: dict[str, Any] = {"status": "running", "active_before": active_before}
    protected = [Path(".env"), Path("configs/reproduce.yaml"), Path("configs/bootstrap.yaml"),
                 Path("backend/src/apex_rag/core/config.py")]
    status["protected_sha256_before"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    try:
        for qi, case in enumerate(cases):
            models = ["text-embedding-v4", "text-embedding-v2"]
            if qi % 2:
                models.reverse()
            for model in models:
                if not available[model]:
                    continue
                http.tag = {"phase": "stage_embedding", "query_id": case["query_id"], "model_call_ordinal": sum(x["model"] == model for x in http.calls) + 1}
                started = time.perf_counter()
                row: dict[str, Any] = {**http.tag, "model": model, "error": None}
                try:
                    vectors = embed(http, model, [case["query"]], "query")
                    query_vectors.append({**http.tag, "model": model, "vectors": vectors})
                except Exception as exc:
                    row["error"] = exc.code if isinstance(exc, ApexError) else type(exc).__name__
                    available[model] = False
                row["elapsed_ms"] = (time.perf_counter() - started) * 1000
                stage.append(row)
                write(output / "stage-results.json", stage)
            pairs = [(m, n) for n in (20, 40, 100) for m in ("qwen3-rerank", "gte-rerank-v2")]
            if qi % 2:
                pairs.reverse()
            for model, depth in pairs:
                if not available[model]:
                    continue
                rows = [lookup[i] for i in case["candidates"][:depth]]
                http.tag = {"phase": "stage_rerank", "query_id": case["query_id"], "depth": depth,
                            "candidate_ids": [r["chunk_id"] for r in rows]}
                started = time.perf_counter()
                row = {**http.tag, "model": model, "error": None}
                try:
                    reranker.model = model
                    ranking = reranker.rerank(case["query"], [r["context_text"] for r in rows], top_n=len(rows))
                    ranked = [{**rows[i], "score": score} for i, score in ranking]
                    row.update(trace=[r.model_dump() for r in trace(ranked)], metrics=retrieval_metrics([r.doc_id for r in aggregate(ranked, 20)], case["relevant_doc_ids"]))
                except Exception as exc:
                    row["error"] = exc.code if isinstance(exc, ApexError) else type(exc).__name__
                    available[model] = False
                row["elapsed_ms"] = (time.perf_counter() - started) * 1000
                stage.append(row)
                write(output / "stage-results.json", stage)
        medians = {m: statistics.median([r["elapsed_ms"] for r in stage if r["model"] == m and not r["error"]]) for m in ("text-embedding-v4", "text-embedding-v2") if available[m]}
        gate = available["text-embedding-v2"] and available["text-embedding-v4"] and medians["text-embedding-v2"] <= .85 * medians["text-embedding-v4"]
        status.update(embedding_stage_medians=medians, alternate_embedding_gate=gate,
                      gate_rule="all5success and v2median<=0.85*v4median; predeclared")
        builds = {"text-embedding-v4": base}
        if gate:
            vectors = []
            for offset in range(0, len(chunks), 10):
                http.tag = {"phase": "isolated_documents", "offset": offset}
                vectors.extend(embed(http, "text-embedding-v2", [c["context_text"] for c in chunks[offset:offset + 10]], "document"))
                fingerprint = hashlib.sha256(json.dumps(["beijing", settings.workspace_id, "text-embedding-v2", 1536, "document", "dense"]).encode()).hexdigest()
                write(output / "isolated-v2-vectors.json", {"model": "text-embedding-v2", "dimension": 1536, "region": "beijing", "embedding_fingerprint": fingerprint, "context_sha256": hashlib.sha256(json.dumps([c["context_text"] for c in chunks]).encode()).hexdigest(), "source_build": manifest["build_id"], "chunk_ids": [c["chunk_id"] for c in chunks[:len(vectors)]], "vectors": vectors})
            at = time.perf_counter()
            name = isolated_table(db, chunks, vectors)
            status["temporary_index_build_ms"] = (time.perf_counter() - at) * 1000
            builds["text-embedding-v2"] = {**base, "table_name": name}
        for qi, case in enumerate(cases):
            combos = [(e, r) for e in builds for r in ("qwen3-rerank", "gte-rerank-v2") if available[e] and available[r]]
            if qi % 2:
                combos.reverse()
            for embedding, rerank in combos:
                http.tag = {"phase": "full_retrieval", "query_id": case["query_id"], "embedding_model": embedding, "rerank_model": rerank, "depth": 40}
                result: dict[str, Any] = {**http.tag, "error": None}
                started = time.perf_counter()
                try:
                    repo.get_build(manifest["build_id"])
                    at = time.perf_counter()
                    vector = embed(http, embedding, [case["query"]], "query")[0]
                    timing = {"embedding": (time.perf_counter() - at) * 1000}
                    query_vectors.append({**http.tag, "vectors": [vector]})
                    at = time.perf_counter()
                    dense = repo.dense(builds[embedding], vector, 100)
                    timing["dense"] = (time.perf_counter() - at) * 1000
                    at = time.perf_counter()
                    sparse = repo.sparse(builds[embedding], case["query"], 100)
                    timing["sparse"] = (time.perf_counter() - at) * 1000
                    at = time.perf_counter()
                    fused = rrf([dense, sparse], 60)[:40]
                    timing["fusion"] = (time.perf_counter() - at) * 1000
                    at = time.perf_counter()
                    reranker.model = rerank
                    ranking = reranker.rerank(case["query"], [r["context_text"] for r in fused], top_n=len(fused))
                    ranked = [{**fused[i], "score": score} for i, score in ranking]
                    timing["rerank"] = (time.perf_counter() - at) * 1000
                    results = aggregate(ranked, 20)
                    timing["retrieval_total"] = (time.perf_counter() - started) * 1000
                    result.update(timings_ms=timing, results=[r.model_dump() for r in results],
                        metrics=retrieval_metrics([r.doc_id for r in results], case["relevant_doc_ids"]),
                        trace={k: [r.model_dump() for r in trace(v)] for k, v in {"dense": dense, "sparse": sparse, "fusion": fused, "rerank": ranked}.items()})
                except Exception as exc:
                    result.update(error=exc.code if isinstance(exc, ApexError) else type(exc).__name__, elapsed_including_failure_ms=(time.perf_counter() - started) * 1000,
                                  metrics=retrieval_metrics([], case["relevant_doc_ids"]))
                full.append(result)
                write(output / "full-results.json", full)
        status.update(status="completed", active_after=repo.get_build()["build_id"])
        if status["active_after"] != active_before:
            raise ValueError("Active build changed concurrently; investigate provenance")
    except Exception as exc:
        status.update(status="failed", error=exc.code if isinstance(exc, ApexError) else type(exc).__name__)
        raise
    finally:
        write(output / "query-vectors.json", query_vectors)
        status.update(logical_calls=len(http.calls), http_attempts=http.attempts,
                      conservative_reserved_cny=http.reserved_cny, actual_billed_cny=None,
                      limits="small fixed exploratory sample; no production P99 or100ms claim; no near-region runtime; serving unchanged")
        status["protected_sha256_after"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
        status["protected_files_unchanged"] = status["protected_sha256_after"] == status["protected_sha256_before"]
        groups = {}
        for e in ("text-embedding-v4", "text-embedding-v2"):
            for r in ("qwen3-rerank", "gte-rerank-v2"):
                rows = [x for x in full if x["embedding_model"] == e and x["rerank_model"] == r]
                if rows:
                    groups[f"{e}/{r}"] = {"count": len(rows), "failed": sum(bool(x["error"]) for x in rows),
                        "metrics": {k: sum(x["metrics"][k] for x in rows) / len(rows) for k in rows[0]["metrics"]},
                        "retrieval_ms": quantiles([x["timings_ms"]["retrieval_total"] for x in rows if not x["error"]]),
                        "database_ms": quantiles([x["timings_ms"]["dense"] + x["timings_ms"]["sparse"] for x in rows if not x["error"]])}
        status["full_groups"] = groups
        write(output / "summary.json", status)
        http.close()
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main(args.output)
