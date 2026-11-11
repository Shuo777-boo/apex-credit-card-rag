"""Parameterized SQL repositories with isolated per-build HNSW/GIN tables."""

import re
from typing import Any

import numpy as np
from psycopg import sql
from psycopg.types.json import Jsonb

from apex_rag.core.errors import ApexError
from apex_rag.schemas import EvaluationDetail, EvaluationItem, EvaluationList
from apex_rag.storage.db import Database


def table_identifier(build: dict[str, Any]) -> sql.Identifier:
    name = build["table_name"]
    if not re.fullmatch(r"chunks_[a-f0-9]{24}", name):
        raise ApexError("BUILD_NOT_READY", "Invalid build table identity")
    return sql.Identifier(name)


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get_build(self, build_id: str | None = None) -> dict[str, Any]:
        """Read explicit or active build metadata; missing builds raise BUILD_NOT_READY."""
        with self.db.connection() as conn:
            row = conn.execute("SELECT b.* FROM index_builds b WHERE b.build_id=COALESCE(%s,(SELECT build_id FROM active_build WHERE singleton))", (build_id,)).fetchone()
        if row is None:
            raise ApexError("BUILD_NOT_READY", "No matching build; run bootstrap before serving queries")
        return dict(row)

    def list_documents(self) -> list[dict[str, Any]]:
        """Return immutable original policy metadata/text ordered by doc_id; no model I/O."""
        with self.db.connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY doc_id").fetchall()]

    def get_document(self, doc_id: str) -> dict[str, Any]:
        """Return original document;404 when absent, never synthesize content from chunks."""
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM documents WHERE doc_id=%s", (doc_id,)).fetchone()
        if row is None:
            raise ApexError("NOT_FOUND", "Document not found", 404)
        return dict(row)

    def list_chunks(self, build_id: str, include_vectors: bool = False) -> list[dict[str, Any]]:
        """Export exact build chunk/source metadata, optionally full vectors for extensions.

        Deterministic doc_id/ordinal ordering; no model call, no dimension conversion. Caller
        must preserve fingerprint/build provenance when exporting to Iceberg or graph.
        """
        build = self.get_build(build_id)
        vector = sql.SQL(",c.embedding") if include_vectors else sql.SQL("")
        with self.db.connection() as conn:
            rows = conn.execute(sql.SQL("SELECT c.chunk_id,c.doc_id,c.ordinal,c.text,c.context_text,c.parent_id,c.start_char,c.end_char,d.title,d.category,d.card_tier,d.effective_year{} FROM {} c JOIN documents d USING(doc_id) ORDER BY c.doc_id,c.ordinal").format(vector, table_identifier(build))).fetchall()
        values = [dict(r) for r in rows]
        for value in values:
            if include_vectors and value["embedding"] is not None:
                value["embedding"] = value["embedding"].tolist()
        return values

    def validate_ready(self, build: dict[str, Any]) -> dict[str, Any]:
        """Check status/counts/vectors/doc coverage and valid HNSW+GIN without model calls.

        Incomplete or missing indexes raise BUILD_NOT_READY; readiness is evidence-backed.
        """
        if build["status"] != "ready":
            raise ApexError("BUILD_NOT_READY", "Build is not ready", details={"build_id": build["build_id"], "status": build["status"]})
        with self.db.connection() as conn:
            counts = conn.execute(sql.SQL("SELECT count(*) AS chunks,count(embedding) AS vectors,count(DISTINCT doc_id) AS documents FROM {}").format(table_identifier(build))).fetchone()
            indexes = conn.execute("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() AND tablename=%s", (build["table_name"],)).fetchall()
            valid = conn.execute("SELECT count(*) AS count FROM pg_index WHERE indrelid=%s::regclass AND NOT indisvalid", (build["table_name"],)).fetchone()
            doc_count = conn.execute("SELECT count(*) AS count FROM documents WHERE dataset_hash=%s", (build["dataset_hash"],)).fetchone()
        assert counts and valid and doc_count
        definitions = [x["indexdef"] for x in indexes]
        if counts["chunks"] != build["expected_chunks"] or counts["vectors"] != counts["chunks"] or counts["documents"] != build["document_count"] or doc_count["count"] != build["document_count"] or valid["count"] or not any("USING hnsw" in x for x in definitions) or not any("USING gin" in x for x in definitions):
            raise ApexError("BUILD_NOT_READY", "Build completeness or index validation failed")
        return dict(counts)

    def dense(self, build: dict[str, Any], vector: list[float], limit: int,
              card_tier: str | None = None, *, exact: bool = False,
              ef_search: int = 40, force_index: bool = False) -> list[dict[str, Any]]:
        """Retrieve cosine chunk neighbors with the same explicit card filter as FTS.

        Parameterized SQL returns similarity1-distance. Exact/forced planner controls are
        transaction-local; iterative HNSW prevents post-filter candidate starvation on0.8.
        """
        predicate = sql.SQL(" AND d.card_tier=%s") if card_tier else sql.SQL("")
        parameters: list[Any] = [np.asarray(vector, dtype=np.float32)]
        if card_tier:
            parameters.append(card_tier)
        parameters += [np.asarray(vector, dtype=np.float32), limit]
        with self.db.connection() as conn:
            with conn.transaction():
                conn.execute("SELECT set_config('hnsw.ef_search',%s,true)", (str(max(ef_search, limit)),))
                if card_tier and not exact:
                    # pgvector >=0.8 resumes ANN scans after post-filtered tuples.
                    conn.execute("SET LOCAL hnsw.iterative_scan='strict_order'")
                if exact:
                    conn.execute("SET LOCAL enable_indexscan=off")
                    conn.execute("SET LOCAL enable_bitmapscan=off")
                elif force_index:
                    conn.execute("SET LOCAL enable_seqscan=off")
                rows = conn.execute(sql.SQL("SELECT c.chunk_id,c.doc_id,c.text,c.context_text,c.parent_id,c.start_char,c.end_char,d.title,d.category,d.card_tier,d.effective_year,1-(c.embedding <=> %s::vector) AS score FROM {} c JOIN documents d USING(doc_id) WHERE c.embedding IS NOT NULL{} ORDER BY c.embedding <=> %s::vector LIMIT %s").format(table_identifier(build), predicate), parameters).fetchall()
        return [dict(r) for r in rows]

    def sparse(self, build: dict[str, Any], query: str, limit: int,
               card_tier: str | None = None) -> list[dict[str, Any]]:
        """Retrieve English weighted tsvector matches by ts_rank_cd (not BM25)."""
        predicate = sql.SQL(" AND d.card_tier=%s") if card_tier else sql.SQL("")
        parameters: list[Any] = [query]
        if card_tier:
            parameters.append(card_tier)
        parameters.append(limit)
        with self.db.connection() as conn:
            rows = conn.execute(sql.SQL("SELECT c.chunk_id,c.doc_id,c.text,c.context_text,c.parent_id,c.start_char,c.end_char,d.title,d.category,d.card_tier,d.effective_year,ts_rank_cd(c.search_vector,q.query) AS score FROM {} c JOIN documents d USING(doc_id) CROSS JOIN websearch_to_tsquery('english',%s) q(query) WHERE c.search_vector @@ q.query{} ORDER BY score DESC,c.chunk_id LIMIT %s").format(table_identifier(build), predicate), parameters).fetchall()
        return [dict(r) for r in rows]

    def exact_vector_search(self, build_id: str, vector: list[float], top_k: int) -> list[dict[str, Any]]:
        """Return top_k unique documents under exact cosine with stable doc/chunk tie order.

        For Iceberg comparison, no API call or ANN approximation; scores are1-distance.
        """
        build = self.get_build(build_id)
        rows = self.dense(build, vector, build["expected_chunks"], exact=True)
        rows.sort(key=lambda x: (-x["score"], x["doc_id"], x["chunk_id"]))
        selected: dict[str, dict[str, Any]] = {}
        for row in rows:
            selected.setdefault(row["doc_id"], row)
        return list(selected.values())[:top_k]

    def list_evaluations(self) -> EvaluationList:
        with self.db.connection() as conn:
            rows = conn.execute("SELECT run_id,status,created_at,metrics FROM evaluation_runs ORDER BY created_at DESC").fetchall()
        return EvaluationList(items=[EvaluationItem(run_id=r["run_id"], status=r["status"], created_at=r["created_at"].isoformat(), summary=r["metrics"]) for r in rows], total=len(rows))

    def get_evaluation(self, run_id: str) -> EvaluationDetail:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM evaluation_runs WHERE run_id=%s", (run_id,)).fetchone()
            if row is None:
                raise ApexError("NOT_FOUND", "Evaluation run not found", 404)
            results = conn.execute("SELECT result FROM evaluation_results WHERE run_id=%s ORDER BY query_id", (run_id,)).fetchall()
        return EvaluationDetail(run_id=run_id, status=row["status"], config=row["config"], metrics=row["metrics"], per_query=[r["result"] for r in results], artifacts=row["artifacts"])

    def save_run(self, run_id: str, status: str, config: dict[str, Any], metrics: dict[str, Any], artifacts: dict[str, str]) -> None:
        """Persist a run lifecycle/metrics snapshot; config is immutable after creation."""
        with self.db.connection() as conn:
            conn.execute("INSERT INTO evaluation_runs(run_id,status,config,metrics,artifacts) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,metrics=excluded.metrics,artifacts=excluded.artifacts", (run_id, status, Jsonb(config), Jsonb(metrics), Jsonb(artifacts)))

    def save_result(self, run_id: str, query_id: str, result: dict[str, Any]) -> None:
        """Commit each completed or failed question independently for audit/recovery."""
        with self.db.connection() as conn:
            conn.execute("INSERT INTO evaluation_results VALUES(%s,%s,%s) ON CONFLICT(run_id,query_id) DO UPDATE SET result=excluded.result", (run_id, query_id, Jsonb(result)))
