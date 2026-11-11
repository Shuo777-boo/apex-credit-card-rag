"""Synchronous provider protocols shared with the extension window."""

from typing import Protocol


class EmbeddingProvider(Protocol):
    model: str
    dimension: int

    def embed(self, texts: list[str], *, input_type: str = "document") -> list[list[float]]:
        """Return one validated full-length embedding for every input, in input order."""
        ...


class RerankerProvider(Protocol):
    model: str

    def rerank(self, query: str, documents: list[str], *, top_n: int) -> list[tuple[int, float]]:
        """Return (zero-based input index, relevance score) in model-ranked order."""
        ...


class LLMProvider(Protocol):
    model: str

    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False,
                 max_output_tokens: int = 2400) -> str:
        """Generate with strict integer1..8192 output budget (default2400); no fallback."""
        ...
