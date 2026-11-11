"""Validate the official corpus and prepare configured dataset directories."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apex_rag.core.config import project_root
from apex_rag.schemas import CardTier


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(pattern=r"^DOC-\d{4}$")
    title: str = Field(min_length=1)
    category: str = Field(min_length=1)
    card_tier: CardTier
    effective_year: int = Field(ge=2000, le=2100)
    content: str = Field(min_length=1)

    def database_row(self, dataset_hash: str) -> dict[str, Any]:
        value = self.model_dump()
        match = re.search(r"Policy Code:\s*([^\.]+)", self.content)
        value.update(content_hash=sha256(self.content.encode()),
                     policy_code=match.group(1).strip() if match else None, dataset_hash=dataset_hash)
        return value


class EvaluationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str
    query: str
    relevant_doc_ids: list[str] = Field(min_length=1)
    gold_response: str


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_official(data_dir: Path | None = None) -> tuple[list[SourceDocument], list[EvaluationQuery], dict[str, str]]:
    """Validate all official labels; labels are never passed to search or embedding."""
    root = project_root()
    directory = data_dir or root / "data"
    kb_path, eval_path = directory / "kb_documents.json", directory / "eval_queries.json"
    kb_bytes, eval_bytes = kb_path.read_bytes(), eval_path.read_bytes()
    documents = [SourceDocument.model_validate(x) for x in json.loads(kb_bytes)]
    queries = [EvaluationQuery.model_validate(x) for x in json.loads(eval_bytes)]
    ids = {d.doc_id for d in documents}
    if len(documents) != 150 or len(ids) != 150:
        raise ValueError("Official corpus must contain 150 unique documents")
    if len(queries) != 30 or len({q.query_id for q in queries}) != 30:
        raise ValueError("Official benchmark must contain 30 unique questions")
    if any(not set(q.relevant_doc_ids) <= ids or len(set(q.relevant_doc_ids)) != len(q.relevant_doc_ids) for q in queries):
        raise ValueError("Invalid relevance labels")
    hashes = {"kb_documents.json": sha256(kb_bytes), "eval_queries.json": sha256(eval_bytes)}
    for filename, digest in hashes.items():
        original = root / "data" / filename
        if original.exists() and sha256(original.read_bytes()) != digest:
            raise ValueError(f"Official copy differs from original: {filename}")
    return documents, queries, hashes


def copy_official(directory: Path) -> dict[str, str]:
    _, _, hashes = load_official()
    directory.mkdir(parents=True, exist_ok=True)
    for filename, digest in hashes.items():
        destination = directory / filename
        if destination.exists() and sha256(destination.read_bytes()) != digest:
            raise ValueError(f"Refusing to overwrite changed dataset copy: {filename}")
        if not destination.exists():
            destination.write_bytes((project_root() / "data" / filename).read_bytes())
    (directory / "official-manifest.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")
    return hashes
