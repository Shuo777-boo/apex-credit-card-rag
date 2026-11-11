"""Search contract used by API, evaluation, GraphRAG, and Judge."""

from typing import Protocol

from apex_rag.schemas import SearchRequest, SearchResponse


class SearchServiceProtocol(Protocol):
    def search(self, request: SearchRequest, *, request_id: str | None = None) -> SearchResponse:
        """Search real active or explicitly specified ready build, returning unique documents."""
        ...
