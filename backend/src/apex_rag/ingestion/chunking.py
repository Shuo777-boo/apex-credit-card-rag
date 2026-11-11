"""Two deterministic, materially different chunk strategies with exact source offsets."""

import re
from dataclasses import dataclass

from apex_rag.ingestion.data import SourceDocument, sha256
from apex_rag.schemas import Strategy


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    ordinal: int
    text: str
    context_text: str
    parent_id: str
    start_char: int
    end_char: int


def context_prefix(document: SourceDocument) -> str:
    match = re.search(r"Policy Code:\s*([^\.]+)", document.content)
    policy_code = match.group(1).strip() if match else "unspecified"
    return (f"Title: {document.title}\nProduct: {document.card_tier}\n"
            f"Category: {document.category}\nEffective year: {document.effective_year}\nPolicy code: {policy_code}\n")


def chunk_document(document: SourceDocument, strategy: Strategy, *, window: int = 300,
                   overlap: int = 60, hierarchy_max: int = 600) -> list[Chunk]:
    if window <= 0 or overlap < 0 or overlap >= window or hierarchy_max < 50:
        raise ValueError("Invalid chunk size/overlap")
    content = document.content
    spans: list[tuple[int, int]] = []
    if strategy == "fixed_window":
        start = 0
        while start < len(content):
            end = min(start + window, len(content))
            spans.append((start, end))
            if end == len(content):
                break
            start += window - overlap
    elif strategy == "policy_hierarchy":
        match = re.search(r"\bDetails:\s*", content)
        start = match.end() if match else 0
        # Retain an entire short Details node. Long nodes pack sentence/paragraph boundaries.
        while start < len(content):
            ceiling = min(start + hierarchy_max, len(content))
            end = ceiling
            if ceiling < len(content):
                boundaries = list(re.finditer(r"(?<=[.!?;])\s+|\n+", content[start:ceiling]))
                if boundaries:
                    end = start + boundaries[-1].end()
            spans.append((start, end))
            start = end
    else:
        raise ValueError("Unknown chunk strategy")
    prefix = context_prefix(document)
    chunks: list[Chunk] = []
    for ordinal, (start, end) in enumerate(spans):
        text = content[start:end]
        identity = f"{document.doc_id}:{strategy}:{window}:{overlap}:{hierarchy_max}:{start}:{end}:{text}"
        chunks.append(Chunk(sha256(identity.encode())[:24], document.doc_id, ordinal,
            text, prefix + text, f"{document.doc_id}:policy", start, end))
    return chunks
