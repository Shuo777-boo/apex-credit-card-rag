import type { AnswerResponse, PerQuery } from "./types";
export function validCitations(answer: AnswerResponse) {
  return answer.citation_valid
    ? answer.citations.filter((c) =>
        answer.retrieval.results.some(
          (d) =>
            d.doc_id === c.doc_id &&
            d.evidence.some((e) => e.chunk_id === c.chunk_id),
        ),
      )
    : [];
}
export function missedDocs(query: PerQuery, k: number) {
  return (query.relevant_doc_ids ?? []).filter(
    (id) => !(query.ranked_doc_ids ?? []).slice(0, k).includes(id),
  );
}
export function rankDelta(current: PerQuery, baseline?: PerQuery) {
  if (!baseline || current.error || baseline.error) return "N/A";
  const delta = current.metrics["recall@5"] - baseline.metrics["recall@5"];
  return !Number.isFinite(delta)
    ? "N/A"
    : delta > 0
      ? "改善"
      : delta < 0
        ? "退化"
        : "持平";
}
