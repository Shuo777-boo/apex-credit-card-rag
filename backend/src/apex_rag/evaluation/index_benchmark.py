"""Isolated HNSW tuning against exact vectors with real plans, sizes and timings."""

import time
from typing import Any

import numpy as np
from psycopg import sql

from apex_rag.evaluation.metrics import quantiles
from apex_rag.evaluation.runner import write_json
from apex_rag.ingestion.data import load_official
from apex_rag.services import Services
from apex_rag.storage.repository import table_identifier


def benchmark_index(services: Services, build_id: str, *, repeats: int = 34) -> dict[str, Any]:
    """Compare nine isolated HNSW settings to exact cosine neighbors on30 real queries.

    Pays for three batches of query embeddings per build (six across two strategies).
    Each group records >=1000 warmed DB roundtrips, separate EXPLAIN, bytes/build ms,
    default and transaction-local forced plans. Temp indexes never replace serving DDL.
    Returns/saves raw measurements and query vectors; this excludes external API latency.
    """
    repository, settings = services.repository, services.settings
    build = repository.get_build(build_id)
    repository.validate_ready(build)
    _, queries, _ = load_official(settings.path(settings.data_dir))
    if repeats * len(queries) < 1000:
        raise ValueError("Index benchmark requires at least 1000 timed observations per parameter group")
    # Query vectors generated once for database-only experiments, never counted as full retrieval.
    vectors = services.embedding.embed([q.query for q in queries], input_type="query")
    chunk_documents = {c["chunk_id"]: c["doc_id"] for c in repository.list_chunks(build_id)}
    rows: list[dict[str, Any]] = []
    with services.db.connection() as conn:
        with conn.transaction():
            conn.execute(sql.SQL("CREATE TEMP TABLE apex_benchmark_chunks ON COMMIT DROP AS SELECT chunk_id,doc_id,embedding FROM {}").format(table_identifier(build)))
            conn.execute("ANALYZE apex_benchmark_chunks")
            exact: list[list[str]] = []
            exact_latency: list[float] = []
            for vector in vectors:
                values = (np.asarray(vector, dtype=np.float32),)
                exact.append([r["chunk_id"] for r in conn.execute("SELECT chunk_id FROM apex_benchmark_chunks ORDER BY embedding <=> %s::vector,chunk_id LIMIT 20", values).fetchall()])
                for _ in range(repeats):
                    at = time.perf_counter()
                    conn.execute("SELECT chunk_id FROM apex_benchmark_chunks ORDER BY embedding <=> %s::vector,chunk_id LIMIT 20", values).fetchall()
                    exact_latency.append((time.perf_counter() - at) * 1000)
            exact_plan = conn.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT chunk_id FROM apex_benchmark_chunks ORDER BY embedding <=> %s::vector,chunk_id LIMIT 20", (np.asarray(vectors[0], dtype=np.float32),)).fetchone()
            for m, construction in [(8, 64), (16, 64), (16, 128)]:
                at = time.perf_counter()
                conn.execute(sql.SQL("CREATE INDEX apex_benchmark_hnsw ON apex_benchmark_chunks USING hnsw(embedding vector_cosine_ops) WITH(m={},ef_construction={})").format(sql.Literal(m), sql.Literal(construction)))
                build_ms = (time.perf_counter() - at) * 1000
                conn.execute("ANALYZE apex_benchmark_chunks")
                size = conn.execute("SELECT pg_relation_size('apex_benchmark_hnsw') AS bytes").fetchone()
                for ef in (20, 40, 80):
                    conn.execute("SELECT set_config('hnsw.ef_search',%s,true)", (str(ef),))
                    query_sql = "SELECT chunk_id FROM apex_benchmark_chunks ORDER BY embedding <=> %s::vector LIMIT 20"
                    sample = (np.asarray(vectors[0], dtype=np.float32),)
                    default_plan = conn.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + query_sql, sample).fetchone()
                    default_latency: list[float] = []
                    for vector in vectors:
                        args = (np.asarray(vector, dtype=np.float32),)
                        conn.execute(query_sql, args).fetchall()  # explicit warmup
                        for _ in range(repeats):
                            at = time.perf_counter()
                            conn.execute(query_sql, args).fetchall()
                            default_latency.append((time.perf_counter() - at) * 1000)
                    conn.execute("SET LOCAL enable_seqscan=off")
                    forced_plan = conn.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) " + query_sql, sample).fetchone()
                    latency: list[float] = []
                    recalls: list[float] = []
                    per_query: list[dict[str, Any]] = []
                    for index, vector in enumerate(vectors):
                        args = (np.asarray(vector, dtype=np.float32),)
                        ann = [r["chunk_id"] for r in conn.execute(query_sql, args).fetchall()]
                        recall = len(set(ann) & set(exact[index])) / len(exact[index])
                        recalls.append(recall)
                        for _ in range(repeats):
                            at = time.perf_counter()
                            conn.execute(query_sql, args).fetchall()
                            latency.append((time.perf_counter() - at) * 1000)
                        per_query.append({"query_id": queries[index].query_id, "ann_chunk_ids": ann,
                            "exact_chunk_ids": exact[index], "ann_recall@20": recall,
                            "business_recall@20_chunks": len({chunk_documents[c] for c in ann} & set(queries[index].relevant_doc_ids)) / len(set(queries[index].relevant_doc_ids))})
                    conn.execute("SET LOCAL enable_seqscan=on")
                    rows.append({"m": m, "ef_construction": construction, "ef_search": ef,
                        "build_ms": build_ms, "index_bytes": size["bytes"] if size else None,
                        "ann_recall@20": sum(recalls) / len(recalls), "default_latency_ms": quantiles(default_latency),
                        "forced_index_latency_ms": quantiles(latency), "default_plan": default_plan,
                        "raw_default_ms": default_latency, "raw_forced_index_ms": latency,
                        "forced_index_plan": forced_plan, "per_query": per_query})
                conn.execute("DROP INDEX apex_benchmark_hnsw")
    output = {"build_id": build_id, "repeats": repeats, "query_count": len(queries), "query_order": [q.query_id for q in queries], "sample_order": "query-major, then repeat 0..repeats-1",
        "exact_latency_ms": quantiles(exact_latency), "raw_exact_ms": exact_latency, "exact_plan": exact_plan,
        "quantile_method": "numpy linear", "scope": "Database roundtrip only, temp isolated HNSW tables; forced plan uses transaction-local enable_seqscan=off; no live index changed", "results": rows}
    path = settings.path(settings.reports_dir) / "index-benchmark" / f"{build_id}.json"
    write_json(path, output)
    write_json(path.with_name(f"{build_id}-query-vectors.json"), {"fingerprint": settings.embedding_fingerprint,
        "queries": [{"query_id": q.query_id, "vector": v} for q, v in zip(queries, vectors, strict=True)]})
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(13, 4), constrained_layout=True)
    labels = [f"{r['m']}/{r['ef_construction']}/{r['ef_search']}" for r in rows]
    axes[0].bar(labels, [r["forced_index_latency_ms"]["p99"] for r in rows])
    axes[0].set_ylabel("Forced HNSW DB P99 ms")
    axes[1].bar(labels, [r["ann_recall@20"] for r in rows])
    axes[1].set_ylabel("ANN neighbor recall@20")
    axes[1].set_ylim(0, 1.05)
    axes[2].bar(labels, [r["index_bytes"] for r in rows])
    axes[2].set_ylabel("Index bytes")
    for axis in axes:
        axis.tick_params(axis="x", rotation=60, labelsize=7)
        axis.set_xlabel("m / construction / search")
    fig.savefig(path.with_suffix(".png"), dpi=160)
    plt.close(fig)
    return {"artifact": str(path), **output}
