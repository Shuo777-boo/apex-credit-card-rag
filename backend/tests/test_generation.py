"""Generation failure evidence retention and refusal behavior with test-only LLMs."""

import json

import pytest

from apex_rag.core.errors import ApexError
from apex_rag.generation.service import AnswerService
from apex_rag.schemas import (
    AnswerRequest,
    Evidence,
    SearchRequest,
    SearchResponse,
    SearchResult,
    Timings,
    Trace,
)


class FixtureSearch:
    def __init__(self, empty=False):
        self.empty = empty

    def search(self, request, *, request_id=None):
        return SearchResponse(request_id=request_id or "test", build_id="a" * 24, mode=request.mode,
            results=[] if self.empty else [SearchResult(doc_id="DOC-1", title="Policy", category="Fees",
                card_tier="Apex Platinum", effective_year=2024, rank=1, score=.9,
                evidence=[Evidence(chunk_id="C1", text="A fee of 3% applies.")])], timings_ms=Timings(retrieval_total=1), trace=Trace())


class FixtureLLM:
    model = "TEST-ONLY"

    def __init__(self, output=None):
        self.output = output
        self.calls = 0

    def complete(self, messages, *, json_mode=False):
        self.calls += 1
        assert "gold_response" not in messages[-1]["content"]
        if self.output is None:
            raise ApexError("PROVIDER_ERROR", "intentional fixture failure", 502)
        return self.output


def test_llm_failure_retains_complete_retrieval_and_no_fabricated_answer():
    with pytest.raises(ApexError) as failure:
        AnswerService(FixtureSearch(), FixtureLLM()).answer(SearchRequest(query="fee"))
    assert failure.value.code == "PROVIDER_ERROR"
    assert failure.value.details["retrieval"]["results"][0]["doc_id"] == "DOC-1"
    assert "answer" not in failure.value.details


def test_no_evidence_never_calls_llm():
    llm = FixtureLLM()
    response = AnswerService(FixtureSearch(empty=True), llm).answer(SearchRequest(query="unsupported"))
    assert response.evidence_insufficient and not response.citations
    assert llm.calls == 0


def test_valid_answer_mapping_and_invalid_ids_preserve_retrieval():
    llm = FixtureLLM(json.dumps({"answer": "费用为3% [1]", "evidence_insufficient": False}))
    response = AnswerService(FixtureSearch(), llm).answer(SearchRequest(query="fee"))
    assert response.citations[0].chunk_id == "C1"
    assert response.citation_valid and response.warnings
    llm.output = json.dumps({"answer": "费用为3% [99]", "evidence_insufficient": False})
    with pytest.raises(ApexError) as invalid:
        AnswerService(FixtureSearch(), llm).answer(SearchRequest(query="fee"))
    assert invalid.value.code == "INVALID_CITATION"
    assert "retrieval" in invalid.value.details


def test_history_rewrite_uses_original_model_then_current_evidence_only():
    class Search(FixtureSearch):
        def search(self, request, **kwargs):
            assert request.query == "What is Apex Platinum annual fee?"
            assert not hasattr(request, "history")
            return super().search(request, **kwargs)

    class LLM:
        model = "TEST-ONLY"
        calls = 0

        def complete(self, messages, *, json_mode=False, max_output_tokens=2400):
            self.calls += 1
            if self.calls == 1:
                assert max_output_tokens == 512
                assert "Apex Platinum" in messages[-1]["content"]
                return '{"query":"What is Apex Platinum annual fee?"}'
            assert max_output_tokens == 2400
            assert "historical-unsupported-claim" not in messages[-1]["content"]
            assert "A fee of 3% applies." in messages[-1]["content"]
            return '{"answer":"费用3% [1]","evidence_insufficient":false}'

    response = AnswerService(Search(), LLM()).answer(AnswerRequest(query="它的年费呢", history=[
        {"role": "user", "content": "Apex Platinum?"}, {"role": "assistant", "content": "historical-unsupported-claim"}]))
    assert response.context.history_messages_used == 2
    assert response.context.original_query == "它的年费呢"
    assert response.context.resolved_query == "What is Apex Platinum annual fee?"
    assert response.context.history_is_policy_evidence is False
    assert response.timings_ms.query_rewrite is not None


def test_bad_history_or_rewrite_explicitly_fails_without_search():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AnswerRequest(query="fee", history=[{"role": "assistant", "content": "wrongrole"}])
    with pytest.raises(ValidationError):
        AnswerRequest(query="fee", history=[{"role": "user" if i % 2 == 0 else "assistant", "content": "a" * 3000} for i in range(6)])

    class Broken:
        def complete(self, *args, **kwargs):
            return '{"query":""}'

    class Never:
        def search(self, *args, **kwargs):
            raise AssertionError("Rewrite failure must not retrieve")

    with pytest.raises(ApexError) as caught:
        AnswerService(Never(), Broken()).answer(AnswerRequest(query="it?", history=[
            {"role": "user", "content": "card"}, {"role": "assistant", "content": "response"}]))
    assert caught.value.code == "QUERY_REWRITE_FAILED"
