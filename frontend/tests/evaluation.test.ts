import { describe, expect, it } from "vitest";
import { missedDocs, rankDelta } from "../src/result-utils";
import type { PerQuery } from "../src/types";
const row: PerQuery = {
  query_id: "TEST",
  query: "Test only",
  relevant_doc_ids: ["A", "B"],
  gold_response: "Test",
  ranked_doc_ids: ["X", "A", "B"],
  metrics: { "recall@5": 1 },
  timings_ms: {},
  error: null,
  retrieval: null,
};
describe("evaluation evidence", () => {
  it("computes missed labels at the selected depth", () => {
    expect(missedDocs(row, 1)).toEqual(["A", "B"]);
    expect(missedDocs(row, 3)).toEqual([]);
  });
  it("preserves improvement, regression and N/A for failed comparisons", () => {
    expect(rankDelta(row, { ...row, metrics: { "recall@5": 0.5 } })).toBe(
      "改善",
    );
    expect(rankDelta({ ...row, metrics: { "recall@5": 0 } }, row)).toBe("退化");
    expect(rankDelta(row, row)).toBe("持平");
    expect(rankDelta(row, { ...row, error: { code: "FAIL" } })).toBe("N/A");
  });
});
