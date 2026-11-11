import { useState } from "react";
import { Button, Card, Collapse, Empty, Space, Table, Tabs, Tag } from "antd";
import { FileTextOutlined } from "@ant-design/icons";
import type { SearchResponse, Timings, Stage } from "./types";
import { formatMs, metric } from "./api";
import Chart from "./Chart";
import GraphPaths from "./GraphPaths";
import DocumentDrawer from "./DocumentDrawer";
const stages: { key: Stage; title: string; score: string }[] = [
  { key: "dense", title: "Dense · 向量召回", score: "余弦相似度" },
  { key: "sparse", title: "Sparse · 全文召回", score: "ts_rank_cd 相关性" },
  { key: "fusion", title: "Fusion · RRF 融合", score: "RRF 排名融合分" },
  { key: "rerank", title: "Rerank · 模型重排", score: "模型相关性分" },
];
export default function RetrievalDetails({retrieval, timings}: {retrieval: SearchResponse; timings: Timings}) {
const [documentId,setDocumentId]=useState<string|null>(null);
const timingRows = [["追问问题改写",timings.query_rewrite],["问题向量化",timings.embedding],["Dense SQL",timings.dense],["Sparse SQL",timings.sparse],["RRF 融合",timings.fusion],["模型重排",timings.rerank],["召回与融合墙钟",timings.recall_fusion],["回答生成",timings.generation]] as const;
return <div className="retrieval-details"><div className="result-meta"><Tag>{retrieval.mode}</Tag><span>request_id: {retrieval.request_id}</span><span>build_id: {retrieval.build_id}</span></div>          <Card
            title={
              <>
                <FileTextOutlined /> 最终文档与证据片段
              </>
            }
            extra={`${retrieval.results.length} 篇文档`}
          >
            {!retrieval.results.length ? (
              <Empty description="没有检索到匹配证据。请调整问题或卡种筛选。" />
            ) : (
              <Collapse
                ghost
                items={retrieval.results.map((d) => ({
                  key: d.doc_id,
                  label: (
                    <div className="evidence-heading">
                      <b>#{d.rank}</b>
                      <span>{d.title}</span>
                      <Tag>{d.card_tier}</Tag>
                      <Tag>{d.effective_year}</Tag>
                    </div>
                  ),
                  extra: (
                    <Button
                      type="link"
                      size="small"
                      onClick={(e) => {
                        e.stopPropagation();
                        setDocumentId(d.doc_id);
                      }}
                    >
                      打开原文
                    </Button>
                  ),
                  children: (
                    <>
                      <Space wrap>
                        <Tag>{d.doc_id}</Tag>
                        <Tag>{d.category}</Tag>
                        <span className="muted">
                          最终
                          {retrieval.mode === "dense"
                            ? "余弦相似度"
                            : retrieval.mode === "hybrid"
                              ? "RRF 融合分"
                              : "模型相关性分"}
                          ：{metric(d.score)}
                        </span>
                      </Space>
                      {d.evidence.map((e) => (
                        <blockquote key={e.chunk_id}>
                          <code>{e.chunk_id}</code>
                          <p>{e.text}</p>
                        </blockquote>
                      ))}
                    </>
                  ),
                }))}
              />
            )}
          </Card>
          <div className="trace-grid">
            <Card
              title="检索阶段排名"
              extra={<span className="muted">分数不代表概率</span>}
            >
              <Tabs
                items={stages.map((stage) => ({
                  key: stage.key,
                  label: stage.title,
                  children: (
                    <>
                      <p className="muted">
                        {stage.score} · 各阶段分数不跨量纲比较
                      </p>
                      <Table
                        size="small"
                        rowKey="chunk_id"
                        pagination={{ pageSize: 5, showSizeChanger: false }}
                        scroll={{ x: 550 }}
                        dataSource={retrieval.trace[stage.key]}
                        locale={{ emptyText: "本次没有该阶段结果 / 不适用" }}
                        columns={[
                          { title: "排名", dataIndex: "rank", width: 60 },
                          {
                            title: "文档",
                            dataIndex: "doc_id",
                            render: (id: string) => (
                              <Button
                                type="link"
                                size="small"
                                onClick={() => setDocumentId(id)}
                              >
                                {id}
                              </Button>
                            ),
                          },
                          {
                            title: "证据块",
                            dataIndex: "chunk_id",
                            ellipsis: true,
                          },
                          {
                            title: stage.score,
                            dataIndex: "score",
                            render: metric,
                          },
                        ]}
                      />
                    </>
                  ),
                }))}
              />
            </Card>
            <Card title="实测耗时" extra={<Tag>ms</Tag>}>
              <div className="timing-summary">
                <span>
                  完整检索 <b>{formatMs(timings?.retrieval_total)}</b>
                </span>
                <span>
                  完整问答 <b>{formatMs(timings?.answer_total)}</b>
                </span>
              </div>
              <Chart
                label="各阶段实测耗时，与下方明细一致"
                option={{
                  grid: { left: 105, right: 30, top: 10, bottom: 25 },
                  tooltip: { trigger: "axis" },
                  xAxis: { type: "value", name: "ms" },
                  yAxis: {
                    type: "category",
                    data: timingRows.map((x) => x[0]),
                    inverse: true,
                  },
                  series: [
                    {
                      type: "bar",
                      data: timingRows.map((x) => x[1] ?? null),
                      itemStyle: {
                        color: "#378bff",
                        borderRadius: [0, 5, 5, 0],
                      },
                      barMaxWidth: 17,
                    },
                  ],
                }}
              />
              <div className="timing-list">
                {timingRows.map(([name, value]) => (
                  <div key={name}>
                    <span>{name}</span>
                    <b>{formatMs(value)}</b>
                  </div>
                ))}
              </div>
              <p className="muted">
                并行阶段不相加作为总耗时。空值表示未测量或不适用。
              </p>
            </Card>
          </div>
{retrieval.mode === "graph_enhanced" && <GraphPaths expansion={retrieval.graph_expansion} paths={retrieval.graph_paths ?? []} open={setDocumentId}/>}
<DocumentDrawer id={documentId} onClose={()=>setDocumentId(null)}/></div>;
}

