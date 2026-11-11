export type Mode = "dense" | "hybrid" | "hybrid_rerank" | "graph_enhanced";
export type CoreMode=Exclude<Mode,'graph_enhanced'>;
export type Strategy='fixed_window'|'policy_hierarchy';
export interface ModelPrice {model:string;input_price:number;unit_tokens:number;output_price:number;billing_rule:string;source_url:string;protocol_url:string;source_sha256:string;protocol_sha256:string}
export interface PricingCatalog {version:string;verified_on:string;region:string;region_name:string;currency:string;invocation:string;applicable:boolean;models:ModelPrice[];notes:string[]}
export interface UsageCost {status:'unknown'|'partial'|'complete';known_usage_cost_cny:number|null;total_usage_cost_cny:number|null;actual_billed_cny:number|null;known_tokens_by_model:Record<string,number>;known_calls:number;unknown_calls:number;valuation_basis:string;notes:string[]}
export interface EvaluationOptions {builds:{build_id:string;strategy:Strategy;documents:number;chunks:number;models:Record<string,string|number>}[];profiles:string[];modes:CoreMode[];candidate_depth:number;rerank_depth:number;top_k:number;questions_per_run:number;limits:{concurrent_jobs:number;single_http_attempts:number;six_http_attempts:number;single_cost_cny:number;six_cost_cny:number;single_timeout_seconds:number;six_timeout_seconds:number;monetary_limits_kind?:string};prices_cny_per_million:Record<string,number>;pricing?:PricingCatalog|null;conversation:{max_messages:number;max_message_chars:number;max_total_chars:number;context_mode:string;history_is_policy_evidence:boolean}}
export type JobRequest={idempotency_key:string}&({profile:'single';build_id:string;mode:CoreMode}|{profile:'six';build_ids:Record<Strategy,string>});
export interface JobEstimate {pricing?:PricingCatalog|null;project_safety_reservation_cny?:number|null;quoted_total_cny?:number|null;quote_status?:'unknown_until_provider_usage';configurations:Record<string,unknown>[];total_questions:number;logical_calls_max:number;http_attempts_max:number;cost_ceiling_cny:number;conservative_estimate_cny:number;retries_max:number;candidate_depth:number;rerank_depth:number;top_k:number;query_cache:boolean;prices_cny_per_million:Record<string,number>;caveats:string[]}
export interface EvaluationJob {pricing?:PricingCatalog|null;usage_cost?:UsageCost;job_id:string;status:'queued'|'running'|'cancelling'|'completed'|'failed'|'cancelled'|'interrupted';progress:{completed_questions:number;total_questions:number;completed_runs:number;total_runs:number;current_query_id:string|null};runs:({run_id:string;status:string}&Record<string,unknown>)[];estimate:JobEstimate;budget:{logical_calls:number;http_attempts:number;reserved_upper_cny:number;actual_billed_cny:number|null;logical_limit:number|null;http_limit:number|null;cost_limit_cny:number|null;pending_request:Record<string,unknown>|null};error:Record<string,unknown>|null;created_at:string;updated_at:string}
export interface SearchRequest {
  query: string;
  mode: Mode;
  top_k: number;
  card_tier: string | null;
}
export interface AnswerRequest extends SearchRequest { history?:{role:'user'|'assistant';content:string}[] }
export interface Document {
  doc_id: string;
  title: string;
  category: string;
  card_tier: string;
  effective_year: number;
  content: string;
}
export interface Evidence {
  chunk_id: string;
  text: string;
  parent_id?: string | null;
  start_char?: number | null;
  end_char?: number | null;
}
export interface RankedChunk {
  chunk_id: string;
  doc_id: string;
  rank: number;
  score: number | null;
}
export type Stage = "dense" | "sparse" | "fusion" | "rerank";
export type Timings = Partial<
  Record<
    | "embedding"
    | "query_rewrite"
    | Stage
    | "recall_fusion"
    | "retrieval_total"
    | "generation"
    | "answer_total",
    number | null
  >
>;
export interface SearchResult extends Omit<Document, "content"> {
  rank: number;
  score: number | null;
  evidence: Evidence[];
}
export interface GraphPath {
  nodes: { id: string; type: string; label: string; doc_id?: string }[];
  edges: {
    source: string;
    target: string;
    relation: string;
    doc_id: string;
    evidence: string;
    extraction_method: string;
    version: string;
    source_sha256: string;
  }[];
  doc_ids: string[];
  hops: number;
}
export interface GraphExpansion {
  seed_doc_ids: string[];
  expanded_doc_ids: string[];
  candidate_doc_ids: string[];
  truncated: boolean;
  visited_nodes: number;
  max_hops: number;
  version: string;
}
export interface SearchResponse {
  request_id: string;
  build_id: string;
  mode: Mode;
  results: SearchResult[];
  timings_ms: Timings;
  trace: Record<Stage, RankedChunk[]>;
  warnings?: string[];
  graph_paths?: GraphPath[];
  graph_expansion?: GraphExpansion | null;
}
export interface Citation {
  id: number;
  doc_id: string;
  chunk_id: string;
  quote: string;
}
export interface AnswerResponse {
  context?: {mode:'single_turn'|'bounded_history_rewrite';history_messages_used:number;original_query:string;retrieval_query:string;resolved_query:string;history_is_policy_evidence:false};
  request_id: string;
  answer: string;
  citations: Citation[];
  retrieval: SearchResponse;
  timings_ms: Timings;
  evidence_insufficient: boolean;
  citation_valid: boolean;
  warnings: string[];
}
export interface ErrorEnvelope {
  request_id: string;
  error: {
    code: string;
    message: string;
    details?: { retrieval?: SearchResponse; [key: string]: unknown };
  };
}
export interface EvaluationItem {
  run_id: string;
  status: string;
  created_at: string;
  summary: Record<string, unknown>;
}
export interface EvaluationList {
  items: EvaluationItem[];
  total: number;
}
export interface LatencyStats {
  p50: number | null;
  p99: number | null;
  samples: number;
}
export interface EvaluationMetrics {
  [key: string]: unknown;
  counts?: { total: number; succeeded: number; failed: number };
  latency_ms?: { database: LatencyStats; retrieval_total: LatencyStats };
}
export interface PerQuery {
  query_id: string;
  query: string;
  relevant_doc_ids: string[];
  gold_response: string;
  ranked_doc_ids: string[];
  metrics: Record<string, number>;
  timings_ms: Timings;
  error: unknown;
  retrieval: SearchResponse | null;
}
export interface EvaluationDetail {
  run_id: string;
  status: string;
  config: {
    strategy?: string;
    mode?: Mode;
    build_id?: string;
    models?: unknown;
    candidate_depth?: number;
    rerank_depth?: number;
    dataset_hashes?: unknown;
    [key: string]: unknown;
  };
  metrics: EvaluationMetrics;
  per_query: PerQuery[];
  artifacts: Record<string, string>;
}
export interface Capabilities {
  graph: {
    available: boolean;
    mode: string;
    max_hops: number;
    requires_ready_build: boolean;
  };
  judge: {
    available: boolean;
    requires_key: boolean;
    human_review_required: number;
  };
  report_schema_version: string;
}
export interface ExtensionItem {
  report_id: string;
  kind: string;
  status: string;
  metrics: Record<string, unknown>;
  schema_version: string;
}
export interface JudgeMetric {
  mean: number | null;
  scored: number;
  na: number;
}
export interface JudgeSummary {
  context_relevance: JudgeMetric;
  faithfulness: JudgeMetric;
  expected: number;
  attempted: number;
  completed: number;
  failed_or_missing: number;
  refusal_rate: number | null;
  answers_question: Record<string, number>;
  citation_count: number;
  valid_citation_count: number;
}
export interface GraphSummary {
  expected: number;
  completed: number;
  document_coverage: number | null;
  citation_coverage_proxy: number | null;
  mean_retrieval_ms: number | null;
  manual_answer_completeness: number | null;
}
export interface JudgeEvidence {evidence_id:string;doc_id:string;title?:string;card_tier?:string;effective_year?:number;text:string;chunk_ids?:string[]}
export interface JudgeSample {sample_id:string;query_id:string;question:string;mode:string;answer:string;citations:Citation[];evidence:JudgeEvidence[];response?:AnswerResponse}
export interface JudgeClaim {claim_id?:string;claim:string;answer_quote?:string;verdict:string;evidence_ids:string[];support_quote?:string;support_quotes?:{evidence_id:string;span_id?:string;start?:number;end?:number;quote:string}[];reason:string;claim_matches_answer?:boolean}
export interface JudgeVerdict {sample_id:string;mode:string;controlled?:boolean;status:string;method_version?:string;phase?:string;attempts?:number;error_type?:string;error_code?:string|null;error?:unknown;context_relevance:number|null;faithfulness:number|null;latency_ms?:number|null;claim_count?:number;citation_validity?:{count:number;valid:number;rate:number|null;meaning?:string};verdict?:{context_relevance:{evidence_id:string;score:number;reason:string}[];claims:JudgeClaim[];answers_question:string;answers_question_reason?:string;refusal?:boolean;no_claims_reason?:string|null}|null}
export interface GraphSample {query_id:string;query:string;mode:string;status:string;document_coverage:number|null;citation_coverage_proxy:number|null;manual_answer_completeness:number|null;response?:AnswerResponse|null;required_documents?:string[];error?:unknown}
export interface ExtensionReport {
  report_id: string;
  kind: string;
  schema_version: string;
  data: {
    status: string;
    method_version?: string;
    source_run?: string;
    method_changed?: boolean;
    original_results_merged?: boolean;
    base_results_reused?: boolean;
    base_status?: string;
    method_note?: string;
    scope?: string;
    metrics?: Record<string, JudgeSummary | GraphSummary>;
    human_review_completed?: number;
    human_review_required?: number;
    self_evaluation_bias?: string;
    samples?: unknown[];
    verdicts?: unknown[];
    human_review?: unknown[];
    controlled_diagnostics?: unknown[];
    [key: string]: unknown;
  };
}
