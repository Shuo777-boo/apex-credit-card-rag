import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
} from "antd";
import { ReloadOutlined, ExperimentOutlined } from "@ant-design/icons";
import { useQueries, useQuery } from "@tanstack/react-query";
import { api, errorText, formatMs, metric } from "./api";
import type { EvaluationDetail, EvaluationItem, EvaluationJob } from "./types";
import Chart from "./Chart";
import ExtensionReports from "./ExtensionReports";
import EvaluationJobs, { completedJobRuns } from './EvaluationJobs';
import RunDetailModal from "./RunDetailModal";
const label = (run: EvaluationDetail) =>
  `${run.config.strategy === "fixed_window" ? "固定窗口" : run.config.strategy === "policy_hierarchy" ? "政策层级" : String(run.config.strategy ?? "未记录分块")} / ${run.config.mode ?? "未记录模式"}`;
export default function Evaluations() {
  const list = useQuery({
    queryKey: ["evaluations"],
    queryFn: ({ signal }) => api.evaluations(signal),
  });
  const [linkedRun] = useState(()=>new URLSearchParams(location.hash.split('?')[1]??'').get('run')??undefined);
  const [selected, setSelected] = useState<string[]>([]);
  const [inspected, setInspected] = useState<string|undefined>(linkedRun);
  const [tab,setTab]=useState('official');
  const [comparisonJob,setComparisonJob]=useState<EvaluationJob>();
  const comparisonRef=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(comparisonJob)comparisonRef.current?.scrollIntoView?.({behavior:'smooth',block:'start'})},[comparisonJob]);
  const compareJob=(job:EvaluationJob)=>{const ids=completedJobRuns(job);if(ids.length<2)return;setInspected(undefined);setTab('official');setSelected(ids);setComparisonJob({...job});list.refetch()};

  useEffect(()=>{const update=()=>{const run=new URLSearchParams(location.hash.split('?')[1]??'').get('run');if(run)setInspected(run)};window.addEventListener('hashchange',update);return()=>window.removeEventListener('hashchange',update)},[]);
  const [k, setK] = useState(5);

  const requestedIds = Array.from(
    new Set([...selected, ...(inspected ? [inspected] : [])]),
  );
  const details = useQueries({
    queries: requestedIds.map((id) => ({
      queryKey: ["evaluation", id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        api.evaluation(id, signal),
    })),
  });
  const loaded = details.flatMap((q) => (q.data ? [q.data] : []));
  const runs = loaded.filter(
    (r) => r.status === "completed" && selected.includes(r.run_id),
  );
  

  const metrics = [`recall@${k}`, `precision@${k}`, `ndcg@${k}`, "mrr@20"];
  const option = useMemo(
    () => ({
      tooltip: { trigger: "axis" },
      legend: { bottom: 0, type: "scroll" },
      grid: { left: 45, right: 20, top: 20, bottom: 85 },
      xAxis: {
        type: "category",
        data: runs.map((r) => `${label(r)}\n${r.run_id}`),
        axisLabel: { fontSize: 9, width: 110, overflow: "truncate" },
      },
      yAxis: { type: "value", min: 0, max: 1 },
      series: metrics.map((name, index) => ({
        name,
        type: "bar",
        data: runs.map((r) =>
          typeof r.metrics[name] === "number" ? r.metrics[name] : null,
        ),
        itemStyle: {
          color: ["#2680ff", "#69b6ff", "#7a78eb", "#50c9b0"][index],
          borderRadius: [3, 3, 0, 0],
        },
        barMaxWidth: 22,
      })),
    }),
    [runs, k],
  );
  return (
    <>
      <section className="page-heading">
        <div>
          <div className="eyebrow">EVALUATION LAB</div>
          <h1>看见每一次检索的真实表现</h1>
          <p>固定题集、统一口径、保留改善与退化，让实验结果可以复核。</p>
        </div>
        <Button
          icon={<ReloadOutlined />}
          onClick={() => {
            list.refetch();
            details.forEach((q) => q.refetch());
          }}
          loading={list.isFetching}
        >
          刷新实验
        </Button>
      </section>
      <EvaluationJobs onRun={id=>{setInspected(id);list.refetch()}} onCompare={compareJob}/>
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: "official",
            label: "官方 30 题 · 核心检索",
            children: (
              <>
                <Card
                  title={
                    <>
                      <ExperimentOutlined /> 实验记录
                    </>
                  }
                  extra={<Tag>2 种分块 × 3 种模式</Tag>}
                >
                  {list.error ? (
                    <Alert
                      type="error"
                      showIcon
                      message="实验查询失败"
                      description={errorText(list.error)}
                    />
                  ) : (
                    <Table<EvaluationItem>
                      rowKey="run_id"
                      loading={list.isPending}
                      dataSource={list.data?.items}
                      rowSelection={{
                        selectedRowKeys: selected,
                        onChange: (keys) => {setSelected(keys.map(String));setComparisonJob(undefined)},
                        getCheckboxProps: (record) => ({
                          disabled: record.status !== "completed",
                        }),
                      }}
                      scroll={{ x: 700 }}
                      locale={{
                        emptyText: (
                          <Empty description="暂无持久化实验。完成后端 CLI 评测后刷新，不以演示数据代替结果。" />
                        ),
                      }}
                      columns={[
                        { title: "run_id", dataIndex: "run_id" },
                        {
                          title: "状态",
                          dataIndex: "status",
                          render: (s) => (
                            <Tag
                              color={
                                s === "completed"
                                  ? "green"
                                  : s === "failed"
                                    ? "red"
                                    : "orange"
                              }
                            >
                              {s}
                            </Tag>
                          ),
                        },
                        {
                          title: "创建时间（北京时间）",
                          dataIndex: "created_at",
                          render: (value: string) => {
                            const date = new Date(value);
                            return Number.isNaN(date.getTime())
                              ? value
                              : date.toLocaleString("zh-CN", {
                                  timeZone: "Asia/Shanghai",
                                  hour12: false,
                                });
                          },
                        },
                        {
                          title: "实验摘要",
                          dataIndex: "summary",
                          render: (s: Record<string, unknown>) => (
                            <Space direction="vertical" size={0}>
                              <span>Recall@5 {metric(s["recall@5"])}</span>
                              <span>MRR@20 {metric(s["mrr@20"])}</span>
                            </Space>
                          ),
                        },
                        {
                          title: "详情",
                          render: (_, row) => (
                            <Button
                              type="link"
                              onClick={() => setInspected(row.run_id)}
                            >
                              查看详情
                            </Button>
                          ),
                        },
                      ]}
                    />
                  )}
                  <p className="muted">
                    勾选已完成实验进行比较；失败或未完成实验保留可见，不以空值当作零分。总记录：
                    {list.data?.total ?? "—"}。
                  </p>
                </Card>
                {details.map((q, index) =>
                  q.error && selected.includes(requestedIds[index]) ? (
                    <Alert
                      key={requestedIds[index]}
                      type="error"
                      className="spaced-alert"
                      message={`${requestedIds[index]} 加载失败`}
                      description={errorText(q.error)}
                    />
                  ) : null,
                )}
                {selected.length > 0 && (
                  <div ref={comparisonRef} className="comparison-target"><Card
                    className="evaluation-card"
                    title="同口径指标比较"
                    loading={details.some((q) => q.isPending)}
                    extra={
                      <Select
                        aria-label="指标 K"
                        value={k}
                        onChange={setK}
                        options={[1, 3, 5].map((v) => ({
                          value: v,
                          label: `K = ${v}`,
                        }))}
                      />
                    }
                  >
                    {comparisonJob&&<section className="task-comparison-scope" aria-label="本任务对比范围"><Space wrap><Tag color="blue">本任务结果对比</Tag><code>{comparisonJob.job_id}</code><Button size="small" onClick={()=>{setSelected([]);setComparisonJob(undefined)}}>清除任务对比</Button></Space><p>仅载入本任务点击时已完成的 {selected.length} 项结果，已替换之前的比较选择；失败或未完成配置保留在下表，不计入图表。后续完成的配置请从任务详情重新选入。</p><Table size="small" rowKey="run_id" pagination={false} scroll={{x:550}} dataSource={comparisonJob.runs} columns={[{title:'本任务 run_id',dataIndex:'run_id'},{title:'保存状态',dataIndex:'status',render:(s:string)=><Tag color={s==='completed'?'success':'warning'}>{s}</Tag>},{title:'比较',render:(_,r)=>selected.includes(r.run_id)?'已选择，成功读取完成结果后入图':'排除（未完成 / 失败）'},{title:'操作',render:(_,r)=><Button type="link" onClick={()=>setInspected(r.run_id)}>查看详情</Button>}]}/>{!details.some(q=>q.isPending)&&runs.length<2&&<Alert type="warning" message="成功读取的已完成结果不足两项，暂不能形成有效对比，请检查上方读取错误或任务状态。"/>}</section>}
                    <p className="muted">
                      MRR 固定深度
                      20；不同数据摘要、模型或候选深度须核对配置后比较。图表与表格读取相同
                      run 和指标值，N/A 留空。
                    </p>
                    <Chart
                      label={`实验 Recall Precision nDCG@${k} 与 MRR@20，与下方表格同源`}
                      option={option}
                    />
                    <Table<EvaluationDetail>
                      rowKey="run_id"
                      size="small"
                      scroll={{ x: 1400 }}
                      dataSource={runs}
                      pagination={false}
                      columns={[
                        {
                          title: "配置 / run_id",
                          fixed: "left",
                          width: 230,
                          render: (_, r) => (
                            <>
                              <b>{label(r)}</b>
                              <div className="muted">{r.run_id}</div>
                            </>
                          ),
                        },
                        ...metrics.map((name) => ({
                          title: name,
                          key: name,
                          render: (_: unknown, r: EvaluationDetail) =>
                            metric(r.metrics[name]),
                        })),
                        {
                          title: "样本 成功/总数（失败）",
                          render: (_, r) =>
                            r.metrics.counts
                              ? `${r.metrics.counts.succeeded}/${r.metrics.counts.total}（${r.metrics.counts.failed}）`
                              : "N/A",
                        },
                        ...(["database", "retrieval_total"] as const).flatMap(
                          (boundary) =>
                            ["p50", "p99", "samples"].map((stat) => ({
                              title: `${boundary === "database" ? "数据库" : "完整检索"} ${stat}`,
                              key: `${boundary}-${stat}`,
                              render: (_: unknown, r: EvaluationDetail) => {
                                const value =
                                  r.metrics.latency_ms?.[boundary]?.[
                                    stat as "p50" | "p99" | "samples"
                                  ];
                                return stat === "samples"
                                  ? (value ?? "N/A")
                                  : formatMs(value);
                              },
                            })),
                        ),
                      ]}
                    />
                    <p className="muted">
                      数据库往返与完整检索边界独立。P99
                      在少量样本下仅供探索；不从数据库低延迟推导完整检索达标。
                    </p>
                  </Card></div>
                )}

              </>
            ),
          },
          {
            key: "graph",
            label: "图谱挑战 · 独立题集",
            children: <ExtensionReports kind="graph" />,
          },
          {
            key: "judge",
            label: "LLM Judge · 回答质量",
            children: <ExtensionReports kind="judge" />,
          },
        ]}
      />
      <RunDetailModal key={inspected??"closed"} id={inspected} onClose={()=>{setInspected(undefined);if(location.hash.includes("?run="))history.replaceState(null,"","#evaluations")}} baselines={runs}/>
    </>
  );
}
