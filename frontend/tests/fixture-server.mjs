// Explicit browser test harness only. Never used by npm run dev or the production app.
// Run after npm run build: node tests/fixture-server.mjs (http://127.0.0.1:15173).
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
const document = {
  doc_id: "TEST-DOC",
  title: "[TEST FIXTURE] Browser citation policy",
  card_tier: "Apex Platinum",
  category: "TEST ONLY",
  effective_year: 2025,
  content:
    "TEST FIXTURE ONLY. This is not an Apex policy. Browser verification: full original text loads from document endpoint.",
};
const retrieval = {
  request_id: "TEST-REQUEST",
  build_id: "TEST-NOT-REAL",
  mode: "hybrid_rerank",
  results: [
    {
      ...document,
      rank: 1,
      score: 0.87,
      evidence: [{ chunk_id: "TEST-CHUNK", text: document.content }],
    },
  ],
  timings_ms: {
    embedding: 50,
    dense: 2,
    sparse: 3,
    fusion: 1,
    rerank: 75,
    retrieval_total: 135,
    recall_fusion: 5,
    generation: null,
    answer_total: null,
  },
  trace: Object.fromEntries(
    ["dense", "sparse", "fusion", "rerank"].map((stage, i) => [
      stage,
      [
        {
          chunk_id: "TEST-CHUNK",
          doc_id: document.doc_id,
          rank: 1,
          score: [0.8, 0.04, 0.032, 0.87][i],
        },
      ],
    ]),
  ),
  graph_paths: [],
  warnings: [
    "TEST FIXTURE ONLY：此数据仅用于浏览器交互验证，不是检索能力或官方评测结果。",
  ],
};
const answer = {
  request_id: "TEST-ANSWER",
  answer:
    '## 测试结论\n这是测试夹具回答，仅验证交互。[1]\n\n### 适用条件\n仅限本地前端测试。\n\n### 金额与期限\nN/A。\n\n<script>alert("unsafe")</script>',
  citations: [
    {
      id: 1,
      doc_id: document.doc_id,
      chunk_id: "TEST-CHUNK",
      quote: document.content,
    },
  ],
  retrieval,
  timings_ms: { ...retrieval.timings_ms, generation: 80, answer_total: 215 },
  evidence_insufficient: false,
  citation_valid: true,
  warnings: [],
};
const detail = {
  run_id: "TEST-COMPLETED",
  status: "completed",
  config: {
    strategy: "fixed_window",
    mode: "dense",
    models: { embedding: "TEST ONLY" },
    dataset_hashes: { official: "TEST-NOT-OFFICIAL" },
  },
  metrics: {
    "recall@1": 0.25,
    "recall@3": 0.5,
    "recall@5": 0.75,
    "precision@1": 0.5,
    "precision@3": 0.3,
    "precision@5": 0.2,
    "ndcg@1": 0.5,
    "ndcg@3": 0.6,
    "ndcg@5": 0.7,
    "mrr@20": 0.8,
    counts: { total: 1, succeeded: 1, failed: 0 },
    latency_ms: {
      database: { p50: 2, p99: 3, samples: 1 },
      retrieval_total: { p50: 135, p99: 135, samples: 1 },
    },
  },
  per_query: [
    {
      query_id: "TEST-Q",
      query: "[TEST ONLY] Is the citation clickable?",
      relevant_doc_ids: [document.doc_id, "TEST-MISSED"],
      gold_response: "TEST ONLY: open the document.",
      ranked_doc_ids: [document.doc_id],
      metrics: { "recall@5": 0.5 },
      timings_ms: retrieval.timings_ms,
      error: null,
      retrieval,
    },
  ],
  artifacts: {},
};
const root = path.resolve("dist");
const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://127.0.0.1");
  const send = (data, status = 200) => {
    res.writeHead(status, {
      "Content-Type": "application/json",
      "X-Test-Fixture": "true",
    });
    res.end(JSON.stringify(data));
  };
  if (url.pathname === "/api/v1/extensions/capabilities")
    return send({
      graph: { available: false },
      judge: { available: false },
      report_schema_version: "TEST",
    });
  if (url.pathname === "/api/v1/readiness")
    return send(
      {
        request_id: "TEST",
        error: { code: "TEST_BUILD", message: "Test harness only" },
      },
      503,
    );
  if (url.pathname === "/api/v1/answer") return send(answer);
  if (url.pathname === "/api/v1/search") return send(retrieval);
  if (url.pathname === "/api/v1/documents/TEST-DOC") return send(document);
  if (url.pathname === "/api/v1/evaluations")
    return send({
      items: [
        {
          run_id: detail.run_id,
          status: "completed",
          created_at: "TEST ONLY",
          summary: { fixture: true },
        },
        {
          run_id: "TEST-FAILED",
          status: "failed",
          created_at: "TEST ONLY",
          summary: { fixture: true },
        },
      ],
      total: 2,
    });
  if (url.pathname === "/api/v1/evaluations/TEST-COMPLETED")
    return send(detail);
  if (url.pathname === "/api/v1/evaluations/TEST-FAILED")
    return send({
      ...detail,
      run_id: "TEST-FAILED",
      status: "failed",
      config: { test_failure: "Fixture provider unavailable" },
      metrics: {},
      per_query: [
        {
          ...detail.per_query[0],
          error: { code: "TEST_ERROR" },
          ranked_doc_ids: [],
        },
      ],
    });
  if (url.pathname === "/api/v1/extensions/reports")
    return send({ items: [], total: 0 });
  if (url.pathname.startsWith("/api/"))
    return send(
      {
        request_id: "TEST",
        error: { code: "TEST_NOT_FOUND", message: "Test endpoint not found" },
      },
      404,
    );
  const target =
    url.pathname === "/"
      ? path.join(root, "index.html")
      : path.resolve(root, "." + decodeURIComponent(url.pathname));
  if (!target.startsWith(root + path.sep)) {
    res.writeHead(404);
    res.end();
    return;
  }
  const file =
    fs.existsSync(target) && fs.statSync(target).isFile()
      ? target
      : path.join(root, "index.html");
  const content = fs.readFileSync(file);
  const ext = path.extname(file);
  res.setHeader(
    "Content-Type",
    {
      ".html": "text/html; charset=utf-8",
      ".js": "application/javascript",
      ".css": "text/css",
    }[ext] || "application/octet-stream",
  );
  res.end(
    ext === ".html"
      ? content
          .toString()
          .replace(
            "<body>",
            '<body><div style="position:fixed;top:0;left:0;right:0;z-index:99999;background:#fff0c2;color:#7a4400;text-align:center;font:12px sans-serif;padding:5px;pointer-events:none">TEST FIXTURE ONLY · 本页为独立交互测试，非真实答案/实验结果</div>',
          )
      : content,
  );
});
server.listen(15173, "127.0.0.1", () =>
  console.log("TEST FIXTURE ONLY http://127.0.0.1:15173"),
);
