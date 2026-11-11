"""Resumable import, cache-backed embedding, explicit index construction and activation."""

import json
import time
from dataclasses import asdict
from typing import Any

import numpy as np
from psycopg import sql
from psycopg.types.json import Jsonb

from apex_rag.core.config import Settings
from apex_rag.core.errors import ApexError
from apex_rag.ingestion.chunking import chunk_document
from apex_rag.ingestion.data import copy_official, load_official, sha256
from apex_rag.providers.aliyun import validate_vectors
from apex_rag.providers.interfaces import EmbeddingProvider
from apex_rag.schemas import Strategy
from apex_rag.storage.repository import Repository, table_identifier


class IngestionPipeline:
    def __init__(self, settings: Settings, repository: Repository, embedding: EmbeddingProvider) -> None:
        self.settings, self.repository, self.embedding = settings, repository, embedding
        self.db = repository.db

    def ingest(self, strategy: Strategy, *, window: int = 300, overlap: int = 60,
               hierarchy_max: int = 600) -> dict[str, Any]:
        """Validate/copy official inputs and resume one deterministic isolated build.

        Writes documents, exact-offset chunks and model/region/dimension-keyed cache.
        Missing vectors call real embedding in batches of ten; each batch commits so a
        failure can resume. No indexes, activation, evaluation or generation are implicit.
        Refuses changed dataset versions in this database; leaves failed builds inactive.
        """
        directory = self.settings.path(self.settings.data_dir)
        hashes = copy_official(directory)
        documents, _, _ = load_official(directory)
        config = {"schema_version": 1, "chunker_version": "context-policy-v2", "strategy": strategy, "window": window, "overlap": overlap,
            "hierarchy_max": hierarchy_max, "models": self.settings.public_models(),
            "embedding_fingerprint": self.settings.embedding_fingerprint, "dataset_hashes": hashes}
        build_id = sha256(json.dumps(config, sort_keys=True).encode())[:24]
        table_name = f"chunks_{build_id}"
        chunks = [chunk for doc in documents for chunk in chunk_document(doc, strategy,
            window=window, overlap=overlap, hierarchy_max=hierarchy_max)]
        table = table_identifier({"table_name": table_name})
        started = time.perf_counter()
        with self.db.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(741938002)")
            existing_hash = conn.execute("SELECT DISTINCT dataset_hash FROM documents").fetchall()
            if any(row["dataset_hash"] != hashes["kb_documents.json"] for row in existing_hash):
                raise ApexError("DATASET_CONFLICT", "Existing documents have another version; use a separate database", 409)
            for doc in documents:
                row = doc.database_row(hashes["kb_documents.json"])
                conn.execute("INSERT INTO documents(doc_id,title,category,card_tier,effective_year,content,content_hash,policy_code,dataset_hash) VALUES(%(doc_id)s,%(title)s,%(category)s,%(card_tier)s,%(effective_year)s,%(content)s,%(content_hash)s,%(policy_code)s,%(dataset_hash)s) ON CONFLICT(doc_id) DO NOTHING", row)
            conn.execute("INSERT INTO index_builds(build_id,table_name,dataset_hash,config,expected_chunks,document_count) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(build_id) DO NOTHING", (build_id, table_name, hashes["kb_documents.json"], Jsonb(config), len(chunks), len(documents)))
            conn.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} (chunk_id text PRIMARY KEY,doc_id text NOT NULL REFERENCES documents(doc_id),ordinal integer NOT NULL,text text NOT NULL,context_text text NOT NULL,parent_id text NOT NULL,start_char integer NOT NULL,end_char integer NOT NULL,embedding vector({}),search_vector tsvector NOT NULL)").format(table, sql.Literal(self.settings.embedding_dimension)))
            lookup = {doc.doc_id: doc for doc in documents}
            for chunk in chunks:
                values = asdict(chunk)
                values["title"] = lookup[chunk.doc_id].title
                conn.execute(sql.SQL("INSERT INTO {}(chunk_id,doc_id,ordinal,text,context_text,parent_id,start_char,end_char,search_vector) VALUES(%(chunk_id)s,%(doc_id)s,%(ordinal)s,%(text)s,%(context_text)s,%(parent_id)s,%(start_char)s,%(end_char)s,setweight(to_tsvector('english',%(title)s),'A') || setweight(to_tsvector('english',%(context_text)s),'B')) ON CONFLICT(chunk_id) DO NOTHING").format(table), values)
        reused, generated = 0, 0
        try:
            pending = []
            for chunk in chunks:
                text_hash = sha256(chunk.context_text.encode())
                with self.db.connection() as conn:
                    current = conn.execute(sql.SQL("SELECT embedding IS NOT NULL AS complete FROM {} WHERE chunk_id=%s").format(table), (chunk.chunk_id,)).fetchone()
                    if current and current["complete"]:
                        reused += 1
                        continue
                    cached = conn.execute("SELECT embedding,dimension FROM embedding_cache WHERE fingerprint=%s AND text_hash=%s", (self.settings.embedding_fingerprint, text_hash)).fetchone()
                    if cached:
                        if cached["dimension"] != self.settings.embedding_dimension:
                            raise ApexError("VECTOR_MISMATCH", "Cached vector dimension mismatch", 409)
                        conn.execute(sql.SQL("UPDATE {} SET embedding=%s WHERE chunk_id=%s").format(table), (cached["embedding"], chunk.chunk_id))
                        reused += 1
                    else:
                        pending.append((chunk, text_hash))
            # v4 allows 10 inputs/request. Commit each completed batch for recoverability.
            for offset in range(0, len(pending), 10):
                batch = pending[offset:offset + 10]
                vectors = self.embedding.embed([chunk.context_text for chunk, _ in batch])
                validate_vectors(vectors, len(batch), self.settings.embedding_dimension)
                with self.db.connection() as conn:
                    for (chunk, text_hash), vector_values in zip(batch, vectors, strict=True):
                        vector = np.asarray(vector_values, dtype=np.float32)
                        conn.execute("INSERT INTO embedding_cache(fingerprint,text_hash,dimension,embedding) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING", (self.settings.embedding_fingerprint, text_hash, self.settings.embedding_dimension, vector))
                        conn.execute(sql.SQL("UPDATE {} SET embedding=%s WHERE chunk_id=%s").format(table), (vector, chunk.chunk_id))
                generated += len(batch)
            with self.db.connection() as conn:
                conn.execute("UPDATE index_builds SET status=CASE WHEN status='ready' THEN 'ready' ELSE 'embedded' END,error_code=NULL,stats=stats || %s WHERE build_id=%s", (Jsonb({"ingestion_ms": (time.perf_counter() - started) * 1000, "generated": generated, "reused": reused}), build_id))
        except Exception as exc:
            with self.db.connection() as conn:
                conn.execute("UPDATE index_builds SET status='failed',error_code=%s WHERE build_id=%s", (getattr(exc, "code", "INGESTION_ERROR"), build_id))
            raise
        output = {"build_id": build_id, "chunks": len(chunks), "documents": len(documents), "generated": generated, "reused": reused, "config": config}
        target = directory / "index_configs"
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{build_id}.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
        return output

    def build_indexes(self, build_id: str, *, m: int = 16, ef_construction: int = 64) -> dict[str, Any]:
        """Build/validate real HNSW+GIN only after every expected vector exists.

        Records actual construction ms/bytes; repeated same settings reuse the index.
        Conflicting existing reloptions raise409 instead of pretending requested DDL ran.
        Does not activate or call models. Returns statistics for this isolated build.
        """
        if not 2 <= m <= 100 or not 4 <= ef_construction <= 1000 or ef_construction < 2 * m:
            raise ValueError("Invalid HNSW construction settings")
        build = self.repository.get_build(build_id)
        table = table_identifier(build)
        started = time.perf_counter()
        hnsw = f"{build['table_name']}_hnsw"
        with self.db.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(741938003)")
            existing = conn.execute("SELECT c.reloptions FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relname=%s AND n.nspname=current_schema()", (hnsw,)).fetchone()
            if existing:
                options = dict(option.split("=", 1) for option in (existing["reloptions"] or []))
                if int(options.get("m", "16")) != m or int(options.get("ef_construction", "64")) != ef_construction:
                    raise ApexError("INDEX_CONFIG_CONFLICT", "Existing HNSW parameters differ; use isolated benchmark or a new build", 409)
            counts = conn.execute(sql.SQL("SELECT count(*) AS n,count(embedding) AS v FROM {}").format(table)).fetchone()
            if not counts or counts["n"] != build["expected_chunks"] or counts["v"] != counts["n"]:
                raise ApexError("BUILD_NOT_READY", "All chunks must be embedded before index construction")
            conn.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} USING hnsw(embedding vector_cosine_ops) WITH (m={},ef_construction={})").format(sql.Identifier(hnsw), table, sql.Literal(m), sql.Literal(ef_construction)))
            conn.execute(sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} USING gin(search_vector)").format(sql.Identifier(f"{build['table_name']}_gin"), table))
            conn.execute(sql.SQL("ANALYZE {}").format(table))
            size = conn.execute("SELECT pg_relation_size(%s::regclass) AS bytes", (hnsw,)).fetchone()
            stats = {"index_build_ms": build["stats"].get("index_build_ms") if existing else (time.perf_counter() - started) * 1000,
                "index_check_ms": (time.perf_counter() - started) * 1000,
                "index_reused": bool(existing), "index_bytes": size["bytes"] if size else None, "m": m, "ef_construction": ef_construction}
            conn.execute("UPDATE index_builds SET status='ready',stats=stats || %s WHERE build_id=%s", (Jsonb(stats), build_id))
        self.repository.validate_ready(self.repository.get_build(build_id))
        return {"build_id": build_id, **stats}

    def activate(self, build_id: str) -> dict[str, str]:
        """Atomically switch the singleton only after ready completeness/index checks.

        Failed or partial builds cannot become defaults; no model calls or old data deletion.
        """
        build = self.repository.get_build(build_id)
        self.repository.validate_ready(build)
        with self.db.connection() as conn:
            conn.execute("INSERT INTO active_build(singleton,build_id) VALUES(true,%s) ON CONFLICT(singleton) DO UPDATE SET build_id=excluded.build_id", (build_id,))
        return {"active_build_id": build_id}
