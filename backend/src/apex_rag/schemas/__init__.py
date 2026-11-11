"""Frozen public API v1 contracts; additive extension fields are optional."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Mode = Literal["dense", "hybrid", "hybrid_rerank", "graph_enhanced"]
Strategy = Literal["fixed_window", "policy_hierarchy"]
CardTier = Literal["Apex Platinum", "Apex Preferred Travel", "Apex Everyday Freedom", "Apex Business Select"]


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=4000)
    mode: Mode = "hybrid_rerank"
    top_k: int = Field(default=5, ge=1, le=20)
    card_tier: CardTier | None = None
    build_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{24}$")


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AnswerRequest(SearchRequest):
    history: list[HistoryMessage] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def bounded_history(self) -> "AnswerRequest":
        if len(self.history) % 2 or any(m.role != ("user" if i % 2 == 0 else "assistant") for i, m in enumerate(self.history)):
            raise ValueError("history must contain at most3 complete user/assistant pairs")
        if sum(len(m.content) for m in self.history) > 12000:
            raise ValueError("history exceeds12000characters")
        return self


class EvaluationJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: UUID
    profile: Literal["single", "six"]
    build_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{24}$")
    mode: Literal["dense", "hybrid", "hybrid_rerank"] | None = None
    build_ids: dict[Strategy, str] | None = None

    @model_validator(mode="after")
    def exact_profile(self) -> "EvaluationJobRequest":
        import re
        if self.profile == "single":
            if not self.build_id or not self.mode or self.build_ids is not None:
                raise ValueError("single requires build_id and mode only")
        elif self.build_id or self.mode or not self.build_ids or set(self.build_ids) != {"fixed_window", "policy_hierarchy"} or any(not re.fullmatch(r"[a-f0-9]{24}", v) for v in self.build_ids.values()):
            raise ValueError("six requires both strategy build_ids only")
        return self


class ModelPrice(BaseModel):
    model: str
    input_price: float
    unit_tokens: int = 1_000_000
    output_price: float = 0
    billing_rule: str
    source_url: str
    protocol_url: str
    source_sha256: str
    protocol_sha256: str


class PricingCatalog(BaseModel):
    version: str
    verified_on: str
    region: str = "cn-beijing"
    region_name: str = "中国内地 · 华北2（北京）"
    currency: str = "CNY"
    invocation: str = "synchronous_online"
    applicable: bool = True
    models: list[ModelPrice]
    notes: list[str]


class UsageCost(BaseModel):
    status: Literal["unknown", "partial", "complete"] = "unknown"
    known_usage_cost_cny: float | None = None
    total_usage_cost_cny: float | None = None
    actual_billed_cny: float | None = None
    known_tokens_by_model: dict[str, int] = Field(default_factory=dict)
    known_calls: int = 0
    unknown_calls: int = 0
    valuation_basis: str = "provider_usage_at_verified_unit_prices"
    notes: list[str] = Field(default_factory=list)


class JobEstimate(BaseModel):
    configurations: list[dict[str, Any]]
    total_questions: int
    logical_calls_max: int
    http_attempts_max: int
    cost_ceiling_cny: float = Field(description="Project budget hard limit; not an official quote")
    conservative_estimate_cny: float = Field(description="Legacy project safety reservation; not an official quote", deprecated=True)
    project_safety_reservation_cny: float | None = None
    quoted_total_cny: float | None = None
    quote_status: Literal["unknown_until_provider_usage"] = "unknown_until_provider_usage"
    pricing: PricingCatalog | None = None
    retries_max: int = 2
    candidate_depth: int = 100
    rerank_depth: int = 100
    top_k: int = 20
    query_cache: bool = False
    prices_cny_per_million: dict[str, float]
    caveats: list[str]


class JobProgress(BaseModel):
    completed_questions: int = 0
    total_questions: int
    completed_runs: int = 0
    total_runs: int
    current_query_id: str | None = None


class JobBudget(BaseModel):
    logical_calls: int = 0
    http_attempts: int = 0
    reserved_upper_cny: float = Field(default=0, description="Project safety reservation, not official price or usage cost")
    actual_billed_cny: float | None = None
    logical_limit: int | None = None
    http_limit: int | None = None
    cost_limit_cny: float | None = None
    pending_request: dict[str, Any] | None = None


class BuildOption(BaseModel):
    build_id: str
    strategy: Strategy
    documents: int
    chunks: int
    models: dict[str, str | int]


class JobLimits(BaseModel):
    concurrent_jobs: int = 1
    single_http_attempts: int = 180
    six_http_attempts: int = 720
    single_cost_cny: float = 3
    six_cost_cny: float = 12
    single_timeout_seconds: int = 900
    six_timeout_seconds: int = 3600
    monetary_limits_kind: str = "project_budget_not_official_quote"


class ConversationLimits(BaseModel):
    max_messages: int = 6
    max_message_chars: int = 4000
    max_total_chars: int = 12000
    context_mode: str = "bounded_history_rewrite"
    history_is_policy_evidence: bool = False


class EvaluationOptions(BaseModel):
    builds: list[BuildOption]
    profiles: list[str] = ["single", "six"]
    modes: list[str] = ["dense", "hybrid", "hybrid_rerank"]
    candidate_depth: int = 100
    rerank_depth: int = 100
    top_k: int = 20
    questions_per_run: int = 30
    limits: JobLimits = Field(default_factory=JobLimits)
    prices_cny_per_million: dict[str, float]
    pricing: PricingCatalog | None = None
    conversation: ConversationLimits = Field(default_factory=ConversationLimits)


class EvaluationJob(BaseModel):
    job_id: str
    status: Literal["queued", "running", "cancelling", "completed", "failed", "cancelled", "interrupted"]
    progress: JobProgress
    runs: list[dict[str, Any]] = Field(default_factory=list)
    estimate: JobEstimate
    budget: JobBudget = Field(default_factory=JobBudget)
    pricing: PricingCatalog | None = None
    usage_cost: UsageCost = Field(default_factory=UsageCost)
    error: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class EvaluationJobList(BaseModel):
    items: list[EvaluationJob]
    total: int


class Document(BaseModel):
    doc_id: str
    title: str
    category: str
    card_tier: str
    effective_year: int
    content: str
    content_hash: str | None = None
    policy_code: str | None = None


class Evidence(BaseModel):
    chunk_id: str
    text: str
    parent_id: str | None = None
    start_char: int | None = None
    end_char: int | None = None


class RankedChunk(BaseModel):
    chunk_id: str
    doc_id: str
    rank: int
    score: float | None = None


class SearchResult(BaseModel):
    doc_id: str
    title: str
    category: str
    card_tier: str
    effective_year: int
    rank: int
    score: float | None = None
    evidence: list[Evidence]


class Timings(BaseModel):
    embedding: float | None = None
    dense: float | None = None
    sparse: float | None = None
    fusion: float | None = None
    rerank: float | None = None
    retrieval_total: float | None = None
    recall_fusion: float | None = None
    generation: float | None = None
    answer_total: float | None = None
    query_rewrite: float | None = None


class Trace(BaseModel):
    dense: list[RankedChunk] = Field(default_factory=list)
    sparse: list[RankedChunk] = Field(default_factory=list)
    fusion: list[RankedChunk] = Field(default_factory=list)
    rerank: list[RankedChunk] = Field(default_factory=list)


class SearchResponse(BaseModel):
    request_id: str
    build_id: str
    mode: Mode
    results: list[SearchResult]
    timings_ms: Timings
    trace: Trace
    graph_paths: list[dict[str, Any]] = Field(default_factory=list)
    graph_expansion: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    id: int
    doc_id: str
    chunk_id: str
    quote: str


class AnswerContext(BaseModel):
    mode: Literal["single_turn", "bounded_history_rewrite"] = "single_turn"
    history_messages_used: int = 0
    original_query: str = ""
    retrieval_query: str = ""
    resolved_query: str = ""
    history_is_policy_evidence: bool = False


class AnswerResponse(BaseModel):
    request_id: str
    answer: str
    citations: list[Citation]
    retrieval: SearchResponse
    timings_ms: Timings
    evidence_insufficient: bool = False
    citation_valid: bool = True
    warnings: list[str] = Field(default_factory=list)
    context: AnswerContext = Field(default_factory=AnswerContext)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    request_id: str
    error: ErrorBody


class LatencySummary(BaseModel):
    p50: float | None = None
    p99: float | None = None
    samples: int = 0


class EvaluationCounts(BaseModel):
    total: int
    succeeded: int
    failed: int


class EvaluationLatency(BaseModel):
    database: LatencySummary
    retrieval_total: LatencySummary


class EvaluationMetrics(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    recall_1: float = Field(alias="recall@1")
    recall_3: float = Field(alias="recall@3")
    recall_5: float = Field(alias="recall@5")
    precision_1: float = Field(alias="precision@1")
    precision_3: float = Field(alias="precision@3")
    precision_5: float = Field(alias="precision@5")
    ndcg_1: float = Field(alias="ndcg@1")
    ndcg_3: float = Field(alias="ndcg@3")
    ndcg_5: float = Field(alias="ndcg@5")
    mrr_20: float = Field(alias="mrr@20")
    counts: EvaluationCounts
    latency_ms: EvaluationLatency


class PerQueryResult(BaseModel):
    query_id: str
    query: str
    relevant_doc_ids: list[str]
    gold_response: str
    ranked_doc_ids: list[str]
    metrics: dict[str, float]
    timings_ms: dict[str, float | None]
    error: dict[str, Any] | None = None
    retrieval: SearchResponse | None = None
    failure_elapsed_ms: float | None = None
    stage_coverage: dict[str, float] = Field(default_factory=dict)


class EvaluationItem(BaseModel):
    run_id: str
    status: str
    created_at: str
    summary: dict[str, Any] = Field(default_factory=dict)


class EvaluationList(BaseModel):
    items: list[EvaluationItem]
    total: int


class EvaluationDetail(BaseModel):
    run_id: str
    status: str
    config: dict[str, Any]
    metrics: EvaluationMetrics | dict[str, Any]
    per_query: list[PerQueryResult]
    artifacts: dict[str, str]


class HealthResponse(BaseModel):
    status: str
    version: str = "0.1.0"
    build_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
