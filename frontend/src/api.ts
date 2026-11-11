import type {
  AnswerResponse,
  AnswerRequest,
  EvaluationOptions, JobRequest, JobEstimate, EvaluationJob,
  Capabilities,
  Document,
  ErrorEnvelope,
  EvaluationList,
  EvaluationDetail,
  ExtensionItem,
  ExtensionReport,
  SearchRequest,
  SearchResponse,
} from "./types";
export class ApiError extends Error {
  constructor(
    public status: number,
    public body: ErrorEnvelope,
  ) {
    super(body.error.message);
    this.name = "ApiError";
  }
}
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new Error("无法连接后端，请确认 8000 端口服务和代理配置。");
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new Error(
      `接口返回非 JSON 内容（HTTP ${response.status}），请检查后端与代理。`,
    );
  }
  if (!response.ok) {
    const candidate = data as Partial<ErrorEnvelope>;
    throw new ApiError(
      response.status,
      candidate.error?.message
        ? (candidate as ErrorEnvelope)
        : {
            request_id: "",
            error: {
              code: `HTTP_${response.status}`,
              message: `请求失败（HTTP ${response.status}）`,
            },
          },
    );
  }
  return data as T;
}
export const api = {
  evaluationOptions:(signal?:AbortSignal)=>request<EvaluationOptions>('/evaluation-options',{signal}),
  estimateJob:(body:JobRequest)=>request<JobEstimate>('/evaluation-jobs/estimate',{method:'POST',body:JSON.stringify(body)}),
  createJob:(body:JobRequest)=>request<EvaluationJob>('/evaluation-jobs',{method:'POST',body:JSON.stringify(body)}),
  evaluationJobs:(signal?:AbortSignal)=>request<{items:EvaluationJob[];total:number}>('/evaluation-jobs',{signal}),
  evaluationJob:(id:string,signal?:AbortSignal)=>request<EvaluationJob>(`/evaluation-jobs/${encodeURIComponent(id)}`,{signal}),
  cancelJob:(id:string)=>request<EvaluationJob>(`/evaluation-jobs/${encodeURIComponent(id)}/cancel`,{method:'POST'}),
  answer: (body: AnswerRequest, signal?: AbortSignal) =>
    request<AnswerResponse>("/answer", {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    }),
  search: (body: SearchRequest, signal?: AbortSignal) =>
    request<SearchResponse>("/search", {
      method: "POST",
      body: JSON.stringify(body),
      signal,
    }),
  document: (id: string, signal?: AbortSignal) =>
    request<Document>(`/documents/${encodeURIComponent(id)}`, { signal }),
  evaluations: (signal?: AbortSignal) =>
    request<EvaluationList>("/evaluations", { signal }),
  evaluation: (id: string, signal?: AbortSignal) =>
    request<EvaluationDetail>(`/evaluations/${encodeURIComponent(id)}`, {
      signal,
    }),
  capabilities: (signal?: AbortSignal) =>
    request<Capabilities>("/extensions/capabilities", { signal }),
  readiness: (signal?: AbortSignal) =>
    request<{ status: string; build_id: string }>("/readiness", { signal }),
  extensionReports: (signal?: AbortSignal) =>
    request<{ items: ExtensionItem[]; total: number }>("/extensions/reports", {
      signal,
    }),
  extensionReport: (id: string, signal?: AbortSignal) =>
    request<ExtensionReport>(`/extensions/reports/${encodeURIComponent(id)}`, {
      signal,
    }),
};
export function formatMs(value: number | null | undefined) {
  return value == null ? "未测量 / 不适用" : `${value.toFixed(2)} ms`;
}
export function metric(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(4)
    : "N/A";
}
export function errorText(error: unknown) {
  if (error instanceof ApiError)
    return `${error.status === 429 ? "请求受到限流，请稍后重试。" : error.message}（${error.body.error.code}）${error.body.request_id ? ` · request_id: ${error.body.request_id}` : ""}`;
  return error instanceof Error ? error.message : "请求失败，请重试。";
}
