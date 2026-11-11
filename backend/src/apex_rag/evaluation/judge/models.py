"""Strict structured verdicts; metric denominators are computed locally."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ContextVerdict(StrictModel):
    evidence_id: str
    score: Literal[0, 1, 2]
    reason: str = Field(min_length=1)


class ClaimVerdict(StrictModel):
    claim: str = Field(min_length=1)
    answer_quote: str = Field(min_length=1)
    verdict: Literal["supported", "unsupported", "contradicted"]
    evidence_ids: list[str]
    support_quote: str
    reason: str = Field(min_length=1)


class JudgeVerdict(StrictModel):
    context_relevance: list[ContextVerdict]
    claims: list[ClaimVerdict]
    answers_question: Literal["yes", "partial", "no"]
    answers_question_reason: str = Field(min_length=1)
    refusal: bool
    no_claims_reason: str | None


def validate_verdict(raw: str, sample: dict) -> JudgeVerdict:
    """Reject missing evidence, fabricated quotes and references outside the sample."""
    result = JudgeVerdict.model_validate_json(raw)
    evidence = {e["evidence_id"]: e["text"] for e in sample["evidence"]}
    ids = [r.evidence_id for r in result.context_relevance]
    if len(ids) != len(set(ids)) or set(ids) != set(evidence):
        raise ValueError("Judge must score each evidence exactly once.")
    if not result.claims and not result.no_claims_reason:
        raise ValueError("No-claim answer requires an explicit reason.")
    seen = set()
    for claim in result.claims:
        if claim.answer_quote not in sample["answer"] or claim.answer_quote in seen:
            raise ValueError("Claims require distinct verbatim answer spans.")
        seen.add(claim.answer_quote)
        if not set(claim.evidence_ids) <= set(evidence):
            raise ValueError("Unknown evidence reference.")
        if claim.verdict in {"supported", "contradicted"}:
            if (
                not claim.evidence_ids
                or not claim.support_quote
                or not any(claim.support_quote in evidence[i] for i in claim.evidence_ids)
            ):
                raise ValueError("Supported/contradicted claim requires a verbatim evidence quote.")
    return result
