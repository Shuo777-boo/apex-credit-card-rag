"""Use Iceberg's manifest-aware scan; never glob Parquet files."""

from __future__ import annotations

import math
import time
import uuid
from pathlib import Path
from typing import Any


def arrow_rows(chunks: list[dict[str, Any]], build: dict[str, Any], model: str) -> Any:
    """Export existing vectors without making any embedding request."""
    import pyarrow as pa

    if not chunks:
        raise ValueError("A complete real core build is required.")
    dimensions = {len(c.get("embedding") or []) for c in chunks}
    if len(dimensions) != 1 or 0 in dimensions:
        raise ValueError("Every chunk must contain a complete, same-dimensional vector.")
    dimension = dimensions.pop()
    schema = pa.schema(
        [
            pa.field("chunk_id", pa.string(), nullable=False),
            pa.field("doc_id", pa.string(), nullable=False),
            pa.field("title", pa.string()),
            pa.field("card_tier", pa.string()),
            pa.field("category", pa.string()),
            pa.field("effective_year", pa.int32()),
            pa.field("text", pa.string()),
            pa.field("model", pa.string()),
            pa.field("dimension", pa.int32()),
            pa.field("build_id", pa.string()),
            pa.field("embedding", pa.list_(pa.float64())),
        ]
    )
    rows = []
    for chunk in chunks:
        vector = [float(v) for v in chunk["embedding"]]
        if not all(math.isfinite(v) for v in vector) or sum(v * v for v in vector) == 0:
            raise ValueError("Embedding must be finite and nonzero.")
        rows.append(
            {
                **{
                    k: chunk.get(k)
                    for k in ("chunk_id", "doc_id", "title", "card_tier", "category", "text")
                },
                "effective_year": int(chunk["effective_year"]),
                "model": model,
                "dimension": dimension,
                "build_id": build["build_id"],
                "embedding": vector,
            }
        )
    return pa.Table.from_pylist(rows, schema=schema)


def cosine_topk(table: Any, vector: list[float], top_k: int = 5) -> dict[str, Any]:
    """Register only the Arrow produced by a valid Iceberg snapshot scan."""
    import duckdb

    if not vector or not all(math.isfinite(x) for x in vector) or sum(x * x for x in vector) == 0:
        raise ValueError("Query vector must be finite and nonzero.")
    if table.num_rows and table.column("dimension")[0].as_py() != len(vector):
        raise ValueError("Query dimension mismatch.")
    if not 1 <= top_k <= 100:
        raise ValueError("top_k must be 1..100.")
    started = time.perf_counter()
    with duckdb.connect(":memory:") as connection:
        connection.register("snapshot_rows", table)
        # Parameters contain all caller values; identifiers and SQL are fixed.
        rows = connection.execute(
            """
            WITH scored AS (
              SELECT doc_id, chunk_id, text, title, card_tier,
                     list_cosine_similarity(embedding, ?::DOUBLE[]) AS score
              FROM snapshot_rows
            ), unique_docs AS (
              SELECT *, row_number() OVER (PARTITION BY doc_id ORDER BY score DESC, chunk_id) AS rn
              FROM scored
            ) SELECT doc_id, chunk_id, text, title, card_tier, score FROM unique_docs
              WHERE rn=1 ORDER BY score DESC, doc_id, chunk_id LIMIT ?
        """,
            [vector, top_k],
        ).fetchall()
    return {
        "results": [
            dict(
                zip(
                    ("doc_id", "chunk_id", "text", "title", "card_tier", "score"), row, strict=False
                )
            )
            for row in rows
        ],
        "ranking_ms": (time.perf_counter() - started) * 1000,
    }


def run_poc(services: Any, directory: Path, *, top_k: int = 5) -> dict[str, Any]:
    """Create an independent table, snapshot update, time travel and pgvector comparison."""
    import pyarrow as pa
    from pyiceberg.catalog import load_catalog

    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    warehouse = directory / "warehouse"
    warehouse.mkdir(exist_ok=True)
    # Fsspec local paths do not unquote percent-encoded non-ASCII path segments.
    # Preserve literal Unicode so artifacts remain inside the requested warehouse.
    warehouse_uri = (
        "file://" + ("/" if not warehouse.as_posix().startswith("/") else "") + warehouse.as_posix()
    )
    catalog = load_catalog(
        "apex_local",
        type="sql",
        uri=f"sqlite:///{(directory / 'catalog.db').as_posix()}",
        warehouse=warehouse_uri,
        **{"py-io-impl": "pyiceberg.io.fsspec.FsspecFileIO"},
    )
    catalog.create_namespace_if_not_exists("apex")
    build = services.repository.get_build()
    services.repository.validate_ready(build)
    if build["config"]["embedding_fingerprint"] != services.settings.embedding_fingerprint:
        raise ValueError("Active vector fingerprint differs from configured provider metadata.")
    chunks = services.repository.list_chunks(build["build_id"], include_vectors=True)
    arrow = arrow_rows(chunks, build, services.embedding.model)
    identifier = f"apex.vectors_{uuid.uuid4().hex[:12]}"
    table = catalog.create_table(identifier, schema=arrow.schema)
    table.append(arrow)
    old_id = table.current_snapshot().snapshot_id
    old_arrow = table.scan(snapshot_id=old_id).to_arrow()
    query_vector = list(chunks[0]["embedding"])  # A real stored vector; no new API cost.
    start = time.perf_counter()
    old_arrow = table.scan(snapshot_id=old_id).to_arrow()
    retrieved = cosine_topk(old_arrow, query_vector, top_k)
    scan_ms = (time.perf_counter() - start) * 1000
    exact_started = time.perf_counter()
    exact = services.repository.exact_vector_search(build["build_id"], query_vector, top_k)
    exact_ms = (time.perf_counter() - exact_started) * 1000
    # Modify a copy only. The explicit marker makes clear it is not official source content.
    changed = old_arrow.to_pylist()
    original = changed[0]["text"]
    changed_chunk_id = changed[0]["chunk_id"]
    changed[0]["text"] = (
        original + " [INDEPENDENT SNAPSHOT DEMO COPY: source text unchanged in core]"
    )
    table.overwrite(pa.Table.from_pylist(changed, schema=arrow.schema))
    new_id = table.current_snapshot().snapshot_id
    old_again = table.scan(snapshot_id=old_id).to_arrow()
    new_arrow = table.scan(snapshot_id=new_id).to_arrow()
    old_text = next(r["text"] for r in old_again.to_pylist() if r["chunk_id"] == changed_chunk_id)
    new_text = next(r["text"] for r in new_arrow.to_pylist() if r["chunk_id"] == changed_chunk_id)
    manifests = [
        {
            "snapshot_id": snapshot.snapshot_id,
            "manifest_list": snapshot.manifest_list,
            "manifests": [manifest.manifest_path for manifest in snapshot.manifests(table.io)],
        }
        for snapshot in table.snapshots()
    ]
    iceberg_ids = [r["doc_id"] for r in retrieved["results"]]
    exact_ids = [r["doc_id"] for r in exact]
    return {
        "status": "completed"
        if iceberg_ids == exact_ids and old_text == original and new_text != old_text
        else "failed",
        "algorithm": "exact cosine scan; not native ANN",
        "build_id": build["build_id"],
        "table": identifier,
        "metadata_location": table.metadata_location,
        "catalog_uri": f"sqlite:///{(directory / 'catalog.db').as_posix()}",
        "warehouse": warehouse_uri,
        "snapshots": manifests,
        "old_snapshot_id": old_id,
        "new_snapshot_id": new_id,
        "time_travel_verified": old_text == original and new_text != old_text,
        "update_demo": {
            "chunk_id": changed_chunk_id,
            "old_text": old_text,
            "new_text": new_text,
            "embedding_policy": "Marker-only independent copy; stored embedding unchanged. Not a semantic policy re-embedding demonstration.",
        },
        "row_count": old_arrow.num_rows,
        "dimension": len(query_vector),
        "query_chunk_id": chunks[0]["chunk_id"],
        "iceberg": retrieved,
        "scan_and_rank_ms": scan_ms,
        "pgvector_exact": exact,
        "pgvector_exact_ms": exact_ms,
        "same_doc_order": iceberg_ids == exact_ids,
        "tie_order": "score DESC, doc_id ASC, chunk_id ASC",
        "limitations": [
            "One stored-vector query; not a query-quality benchmark.",
            "Independent local snapshot copy; no object-store throughput claim.",
            "Exact scanning is linear; no million-scale latency claim.",
        ],
    }
