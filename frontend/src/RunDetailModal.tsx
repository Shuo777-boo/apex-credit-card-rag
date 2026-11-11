import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Alert, Button, Card, Empty, Modal, Select, Skeleton, Space, Table, Tag } from 'antd';
import { api, errorText, formatMs, metric } from './api';
import type { EvaluationDetail, PerQuery } from './types';
import { AdvancedAudit, Bars, ConfigFacts, numeric, QualityMetrics } from './ReportVisuals';
import { missedDocs, rankDelta } from './result-utils';
import DocumentDrawer from './DocumentDrawer';
import RetrievalDetails from './RetrievalDetails';
export default function RunDetailModal({id,onClose,baselines}:{id?:string;onClose:()=>void;baselines:EvaluationDetail[]}){
 const query=useQuery({queryKey:['evaluation',id],queryFn:({signal})=>api.evaluation(id!,signal),enabled:!!id});
 const [baselineId,setBaselineId]=useState<string>();const [question,setQuestion]=useState<PerQuery>();const [documentId,setDocumentId]=useState<string|null>(null);
 const run=query.data,baseline=baselines.find(b=>b.run_id===baselineId);
 const close=()=>{setQuestion(undefined);setDocumentId(null);onClose()};
 return <><Modal title={`实验详情 · ${id??''}`} open={!!id} onCancel={close} footer={<Button onClick={close}>关闭详情</Button>} width="min(1200px, 96vw)" className="report-modal" destroyOnHidden>
  {query.isPending&&<Skeleton active/>}{query.error&&<Alert type="error" message="实验详情读取失败" description={errorText(query.error)} action={<Button onClick={()=>query.refetch()}>重试</Button>}/>}
  {run&&<div key={run.run_id}><Space wrap><Tag>{run.status}</Tag><code>{run.run_id}</code><Button onClick={()=>query.refetch()}>刷新详情</Button></Space><h3>质量指标与中文含义</h3>{run.status!=='completed'&&<Alert type="warning" message="实验未成功完成，以下保留已有数据，不混入已完成对比"/>}<QualityMetrics data={run.metrics}/>
   <div className="report-two-columns">{(['database','retrieval_total'] as const).map(boundary=>{const latency=run.metrics.latency_ms?.[boundary];return <Card key={boundary} title={boundary==='database'?'数据库查询延迟':'完整检索延迟'}><Bars label={`${boundary} P50/P99 原值`} categories={['P50','P99']} unit="ms" series={[{name:'耗时',values:[numeric(latency?.p50),numeric(latency?.p99)]}]}/><p>P50 {formatMs(latency?.p50)} · P99 {formatMs(latency?.p99)} · 样本 {latency?.samples??'N/A'}</p></Card>})}</div>
   <p>题目：成功 {run.metrics.counts?.succeeded??'N/A'} / 总数 {run.metrics.counts?.total??'N/A'} · 失败 {run.metrics.counts?.failed??'N/A'}</p><p className="muted">数据库和完整检索计时边界独立。少量样本 P99 仅供探索，不代表100ms已达标。</p>
   <Card title="实验配置"><ConfigFacts data={run.config}/></Card>
   <Card className="evaluation-card" title="逐题检查与可追溯配置" extra={<Select aria-label="对照实验" allowClear placeholder="选基线比较 Recall@5" value={baselineId} onChange={setBaselineId} style={{width:240,maxWidth:'100%'}} options={baselines.filter(b=>b.run_id!==id).map(b=>({value:b.run_id,label:b.run_id}))}/>}>
    <Table<PerQuery> size="small" rowKey="query_id" dataSource={run.per_query} scroll={{x:950}} pagination={{pageSize:5,showSizeChanger:false}} columns={[{title:'官方原题',render:(_,q)=><><Tag>{q.query_id}</Tag><div>{q.query}</div></>,width:320},{title:'Recall@5',render:(_,q)=>metric(q.metrics['recall@5'])},{title:'漏检@5',render:(_,q)=>missedDocs(q,5).join(' / ')||'无'},{title:'相对基线',render:(_,q)=>rankDelta(q,baseline?.per_query.find(b=>b.query_id===q.query_id))},{title:'状态',render:(_,q)=><Tag color={q.error?'error':'success'}>{q.error?'失败':'成功'}</Tag>},{title:'操作',render:(_,q)=><Button onClick={()=>setQuestion(q)}>查看题目详情</Button>}]}/>
   </Card><Card title="报告下载" className="evaluation-card">{Object.keys(run.artifacts).length?Object.entries(run.artifacts).map(([name,path])=><p key={name}><a href={`/api/v1/evaluations/${encodeURIComponent(run.run_id)}/artifacts/${encodeURIComponent(name)}`} target="_blank" rel="noopener noreferrer">{name} ↗</a> <span className="muted">{path}</span></p>):<Empty description="未登记报告产物"/>}</Card><AdvancedAudit data={run as unknown as Record<string,unknown>}/>
  </div>}
 </Modal><Modal title={`题目详情 · ${question?.query_id??''}`} open={!!id&&!!question} onCancel={()=>{setQuestion(undefined);setDocumentId(null)}} footer={<Button onClick={()=>{setQuestion(undefined);setDocumentId(null)}}>返回实验详情</Button>} width="min(1150px, 94vw)" zIndex={1150} className="report-modal" destroyOnHidden>
 {question&&<><h3>{question.query}</h3><p>相关文档标签：</p><Space wrap>{question.relevant_doc_ids.map(doc=><Button key={doc} onClick={()=>setDocumentId(doc)}>{doc}</Button>)}</Space><p>实际排名（绿色为相关标签命中）：</p><Space wrap>{question.ranked_doc_ids.map((doc,i)=><Tag color={question.relevant_doc_ids.includes(doc)?'success':undefined} key={doc} onClick={()=>setDocumentId(doc)} className="clickable-tag">{i+1} · {doc}</Tag>)}</Space><p>参考回答（英文原文）：{question.gold_response}</p>{question.error!=null&&<Alert type="error" message="本题失败" description={typeof question.error==='object'?String((question.error as Record<string,unknown>).message??'详见高级审计'):String(question.error)}/>}
 <QualityMetrics data={question.metrics}/>{question.retrieval?<RetrievalDetails retrieval={question.retrieval} timings={question.timings_ms}/>:<><Bars label="逐题阶段耗时" categories={Object.keys(question.timings_ms)} unit="ms" series={[{name:'实测耗时',values:Object.values(question.timings_ms).map(numeric)}]}/><Empty description="本题没有保存检索证据"/></>}<AdvancedAudit data={question as unknown as Record<string,unknown>}/></>}
 </Modal><DocumentDrawer id={documentId} onClose={()=>setDocumentId(null)}/></>;
}
