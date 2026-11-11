import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Workbench from "../src/Workbench";
import { validCitations } from "../src/result-utils";
import { api, ApiError, formatMs } from "../src/api";
import type { AnswerResponse, SearchResponse } from "../src/types";
vi.mock("../src/Chart", () => ({ default: () => <div>耗时图</div> }));
// Fixtures are confined to tests. The production app has no mock transport or fallback.
const retrieval: SearchResponse = {
  request_id: "TEST-ONLY",
  build_id: "test-build",
  mode: "dense",
  results: [
    {
      doc_id: "DOC-TEST",
      title: "TEST POLICY",
      category: "Test",
      card_tier: "Apex Platinum",
      effective_year: 2025,
      rank: 1,
      score: 0.8,
      evidence: [{ chunk_id: "TEST-CHUNK", text: "Fixture evidence only." }],
    },
  ],
  timings_ms: { dense: 3, sparse: null, retrieval_total: 110 },
  trace: {
    dense: [
      { doc_id: "DOC-TEST", chunk_id: "TEST-CHUNK", rank: 1, score: 0.8 },
    ],
    sparse: [],
    fusion: [],
    rerank: [],
  },
};
const answer: AnswerResponse = {
  request_id: "TEST-ANSWER",
  answer: "Policy conclusion [1]. <script>window.evil=true</script>",
  citations: [
    {
      id: 1,
      doc_id: "DOC-TEST",
      chunk_id: "TEST-CHUNK",
      quote: "Fixture evidence only.",
    },
  ],
  retrieval,
  timings_ms: { answer_total: 200 },
  evidence_insufficient: false,
  citation_valid: true,
  warnings: [],
};
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Workbench />
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.restoreAllMocks();
});
function setup() {
  vi.spyOn(api, "readiness").mockRejectedValue(
    new Error("Test build not ready"),
  );
  vi.spyOn(api, "capabilities").mockResolvedValue({
    graph: {
      available: false,
      mode: "graph_enhanced",
      max_hops: 2,
      requires_ready_build: true,
    },
    judge: { available: true, requires_key: true, human_review_required: 10 },
    report_schema_version: "bonus-v1",
  });
}
describe("question flow", () => {
  it("opens only a current valid citation and never executes HTML", async () => {
    setup();
    vi.spyOn(api, "answer").mockResolvedValue(answer);
    const doc = vi
      .spyOn(api, "document")
      .mockResolvedValue({
        doc_id: "DOC-TEST",
        title: "TEST POLICY",
        category: "Test",
        card_tier: "Apex Platinum",
        effective_year: 2025,
        content: "FULL TEST POLICY CONTENT",
      });
    mount();
    fireEvent.change(screen.getByLabelText("你想了解什么政策？"), {
      target: { value: "Test policy?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    const citation = await screen.findByRole("button", {
      name: "引用 1，打开 DOC-TEST",
    });
    fireEvent.click(screen.getByRole('button',{name:'引用 1，打开 DOC-TEST'}));
    await waitFor(()=>expect(doc).toHaveBeenCalled());
    expect(await screen.findByText("FULL TEST POLICY CONTENT")).toBeTruthy();
    expect(doc).toHaveBeenCalledWith("DOC-TEST", expect.any(AbortSignal));
    expect(document.querySelector("script")).toBeNull();
  });
  it("keeps retrieved evidence after generation fails, with no fake answer or 0 ms", async () => {
    setup();
    vi.spyOn(api, "answer").mockRejectedValue(
      new ApiError(502, {
        request_id: "TEST-FAIL",
        error: {
          code: "PROVIDER_ERROR",
          message: "Generation failed",
          details: { retrieval },
        },
      }),
    );
    mount();
    fireEvent.change(screen.getByLabelText("你想了解什么政策？"), {
      target: { value: "Test?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送消息" }));
    expect(
      await screen.findByText("生成失败，已保留成功检索到的证据"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name:"查看本轮证据与检索过程"}));
    expect(await screen.findByText("TEST POLICY")).toBeTruthy();
    expect(screen.getByText(/Generation failed/)).toBeTruthy();
    expect(screen.queryByText("Policy conclusion")).toBeNull();
    expect(formatMs(null)).toBe("未测量 / 不适用");
    expect(formatMs(0)).toBe("0.00 ms");
  });
  it("blocks duplicate submits and aborts an active request", async () => {
    setup();
    let signal: AbortSignal | undefined;
    const send = vi
      .spyOn(api, "answer")
      .mockImplementation((_body, supplied) => {
        signal = supplied;
        return new Promise((_resolve, reject) =>
          supplied?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          ),
        );
      });
    mount();
    fireEvent.change(screen.getByLabelText("你想了解什么政策？"), {
      target: { value: "Test?" },
    });
    const start = screen.getByRole("button", { name: "发送消息" });
    fireEvent.click(start);
    fireEvent.keyDown(screen.getByLabelText('你想了解什么政策？'),{key:'Enter'});
    fireEvent.click(await screen.findByRole("button", { name: "停止等待" }));
    await waitFor(() => expect(signal?.aborted).toBe(true));
    expect(send).toHaveBeenCalledTimes(1);
    expect(await screen.findByText("等待已中断")).toBeTruthy();
  });
  it("rejects citations that do not belong to the current result", () => {
    expect(
      validCitations({
        ...answer,
        citations: [
          ...answer.citations,
          { id: 9, doc_id: "OTHER", chunk_id: "BAD", quote: "bad" },
        ],
      }),
    ).toHaveLength(1);
    expect(validCitations({ ...answer, citation_valid: false })).toEqual([]);
  });
});


