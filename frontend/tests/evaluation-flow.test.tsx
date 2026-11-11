import { ConfigProvider } from 'antd';
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Evaluations from "../src/Evaluations";
import { api } from "../src/api";
import type { EvaluationDetail } from "../src/types";
vi.mock("../src/Chart", () => ({
  default: ({ option }: { option: unknown }) => (
    <div data-testid="chart-data">{JSON.stringify(option)}</div>
  ),
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const run: EvaluationDetail = {
  run_id: "TEST-completed",
  status: "completed",
  config: { strategy: "fixed_window", mode: "dense" },
  metrics: {
    "recall@5": 0.75,
    "precision@5": 0.3,
    "ndcg@5": 0.6,
    "mrr@20": 0.8,
    counts: { total: 2, succeeded: 2, failed: 0 },
    latency_ms: {
      database: { p50: 2, p99: 3, samples: 2 },
      retrieval_total: { p50: 100, p99: 120, samples: 2 },
    },
  },
  per_query: [],
  artifacts: {},
};
function mount() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ConfigProvider theme={{token:{motion:false}}} wave={{disabled:true}}><Evaluations /></ConfigProvider>
    </QueryClientProvider>,
  );
}
it("uses the same persisted metric values in chart and table", async () => {
  vi.spyOn(api, "evaluations").mockResolvedValue({
    items: [
      {
        run_id: run.run_id,
        status: "completed",
        created_at: "TEST",
        summary: {},
      },
    ],
    total: 1,
  });
  vi.spyOn(api, "evaluation").mockResolvedValue(run);
  mount();
  await screen.findByText("TEST-completed");
  const boxes = document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
  fireEvent.click(boxes[1]);
  expect(screen.queryByRole("dialog")).toBeNull();
  const chart = await screen.findByTestId("chart-data");
  const data = JSON.parse(chart.textContent!);
  expect(data.series[0].data).toEqual([0.75]);
  expect(await screen.findByText("0.7500")).toBeTruthy();
  expect(screen.getByText("100.00 ms")).toBeTruthy();
});
it("allows failed details while keeping failed runs out of comparison", async () => {
  vi.spyOn(api, "evaluations").mockResolvedValue({
    items: [
      {
        run_id: "TEST-failed",
        status: "failed",
        created_at: "TEST",
        summary: { error: "failure recorded" },
      },
    ],
    total: 1,
  });
  const detail = vi
    .spyOn(api, "evaluation")
    .mockResolvedValue({
      ...run,
      run_id: "TEST-failed",
      status: "failed",
      config: { failure_reason: "provider unavailable" },
    });
  mount();
  await screen.findByText("TEST-failed");
  expect(
    (document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')[1]).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByText("查看详情").closest("button")!);
  expect(await screen.findByText("逐题检查与可追溯配置")).toBeTruthy();
  expect(detail).toHaveBeenCalledWith("TEST-failed", expect.any(AbortSignal));
  expect(screen.getByText("实验详情 · TEST-failed").closest('[role=dialog]')).toBeTruthy();
  expect(screen.queryByText("同口径指标比较")).toBeNull();
});
