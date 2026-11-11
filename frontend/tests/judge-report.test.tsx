import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ExtensionReports from "../src/ExtensionReports";
import { api } from "../src/api";
import type { ExtensionReport } from "../src/types";
vi.mock('../src/Chart',()=>({default:()=> <div/>}));
vi.setConfig({testTimeout:30_000});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("distinguishes pilot, validated completion pending human, and the preserved old method", async () => {
  const items = ["TEST-new", "TEST-old"].map((report_id) => ({
    report_id,
    kind: "judge",
    status: "incomplete",
    metrics: {},
    schema_version: "bonus-v1",
  }));
  vi.spyOn(api, "extensionReports").mockResolvedValue({ items, total: 2 });
  let complete = false;
  let recovery = false;
  vi.spyOn(api, "extensionReport").mockImplementation(
    async (id) =>
      ({
        report_id: id,
        kind: "judge",
        schema_version: "bonus-v1",
        data:
          id === "TEST-old"
            ? {
                status: "incomplete",
                human_review_completed: 0,
                human_review_required: 10,
              }
            : {
                status: complete ? "completed_pending_human" : "incomplete",
                method_version: recovery
                  ? "qwen-judge-v2.2-anchors+recovery-v1"
                  : "qwen-judge-v2.2-anchors",
                source_run: "TEST-old",
                original_results_merged: false,
                scope: recovery
                  ? "full_saved_60_with_targeted_recovery"
                  : complete
                    ? "full_saved_60"
                    : "pilot",
                base_results_reused: recovery,
                base_status: recovery ? "incomplete" : undefined,
                human_review_completed: 0,
                human_review_required: 10,
              },
      }) satisfies ExtensionReport,
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ExtensionReports kind="judge" />
    </QueryClientProvider>,
  );
  const newRow = await screen.findByRole("row", { name: /TEST-new/ });
  fireEvent.click(within(newRow).getByRole("button", { name: "查看报告" }));
  expect(
    await screen.findByText("方法版本：qwen-judge-v2.2-anchors"),
  ).toBeTruthy();
  expect(screen.getByText("评审范围：试跑 · 不代表全批完成")).toBeTruthy();
  fireEvent.click(screen.getByText('方法与限制'));
  expect(screen.getByText("原结果未混入本次指标")).toBeTruthy();
  complete = true;
  fireEvent.click(screen.getByRole("button", { name: "刷新报告" }));
  expect(await screen.findByText("自动评审完整，待人工复核")).toBeTruthy();
  expect(screen.getByText("人工复核：0 / 10")).toBeTruthy();
  recovery = true;
  fireEvent.click(screen.getByRole("button", { name: "刷新报告" }));
  expect(
    await screen.findByText("评审范围：完整 60 条已保存回答 · 定向失败项补评"),
  ).toBeTruthy();
  expect(
    screen.getByText("组合报告：复用基础批次成功项，仅失败项获得额外尝试"),
  ).toBeTruthy();
  expect(screen.queryByText("原结果未混入本次指标")).toBeNull();
  fireEvent.click(screen.getByRole('button',{name:'关闭报告'}));
  await new Promise(resolve=>setTimeout(resolve,350));
  fireEvent.click(
    within(screen.getByRole("row", { name: /TEST-old/ })).getByRole("button", {
      name: "查看报告",
    }),
  );
  expect(await screen.findByText("方法版本：未记录（旧报告）")).toBeTruthy();
  expect(screen.getByText("自动评审未完整，请核对失败或缺失样本")).toBeTruthy();
  expect(screen.queryByText("方法版本：qwen-judge-v2.2-anchors")).toBeNull();
});
