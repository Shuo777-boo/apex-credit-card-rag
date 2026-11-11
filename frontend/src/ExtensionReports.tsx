import { useState } from 'react';
import { Alert, Button, Card, Collapse, Empty, Modal, Select, Skeleton, Space, Table, Tag } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { api, errorText } from './api';
import type { ExtensionItem } from './types';
import { AdvancedAudit, ConfigFacts } from './ReportVisuals';
import JudgeVisualReport from './JudgeVisualReport';
import GraphVisualReport from './GraphVisualReport';
import DocumentDrawer from './DocumentDrawer';
export default function ExtensionReports({kind}:{kind:'graph'|'judge'}){
 const [id,setId]=useState<string>(),[documentId,setDocumentId]=useState<string|null>(null);
 const list=useQuery({queryKey:['extension-reports'],queryFn:({signal})=>api.extensionReports(signal)});
 const report=useQuery({queryKey:['extension-report',id],queryFn:({signal})=>api.extensionReport(id!,signal),enabled:!!id});
 const data=report.data&&report.data.report_id===id?report.data.data:undefined;
 const items=list.data?.items.filter(i=>i.kind===kind)??[];
 const choose=(next:string)=>{setDocumentId(null);setId(next)};
 const close=()=>{setDocumentId(null);setId(undefined)};
 const scope=data?.scope==='pilot'?'试跑 · 不代表全批完成':data?.scope==='full_saved_60'?'完整 60 条已保存回答':data?.scope==='full_saved_60_with_targeted_recovery'?'完整 60 条已保存回答 · 定向失败项补评':data?.scope??'未记录';
 return <><p className="muted">{kind==='graph'?'独立挑战题集 · 文档覆盖为代理指标，答案完整度以人工复核为准。':'独立回答质量评估 · N/A 不计作满分，自动评审与人工复核分别展示。'}</p>
 <Card title={kind==='graph'?'图谱挑战报告':'LLM Judge 报告'} extra={<Button loading={list.isFetching} onClick={()=>list.refetch()}>刷新报告列表</Button>}>
 {list.error?<Alert type="error" message="扩展报告查询失败" description={errorText(list.error)} action={<Button onClick={()=>list.refetch()}>重试</Button>}/>:<Table<ExtensionItem> rowKey="report_id" loading={list.isPending} dataSource={items} pagination={{pageSize:5,showSizeChanger:false}} scroll={{x:600}} locale={{emptyText:<Empty description="暂无真实报告，页面不会自动触发实验"/>}} columns={[{title:'报告 / run_id',dataIndex:'report_id'},{title:'状态',dataIndex:'status',render:s=><Tag>{s}</Tag>},{title:'Schema',dataIndex:'schema_version'},{title:'操作',render:(_,r)=><Button type="link" onClick={()=>choose(r.report_id)}>查看报告</Button>}]}/>}
 </Card>
 <Modal title={`报告 · ${id??''}`} open={!!id} onCancel={close} footer={<Button onClick={close}>关闭报告</Button>} width="min(1240px, 96vw)" className="report-modal" destroyOnHidden>
 <Space wrap className="spaced-alert"><Select aria-label="切换报告" value={id} onChange={choose} options={items.map(i=>({value:i.report_id,label:i.report_id}))} style={{width:350,maxWidth:'100%'}}/><Button loading={report.isFetching} onClick={()=>report.refetch()}>刷新报告</Button></Space>
 {report.isPending&&<Skeleton active/>}{report.error&&<Alert type="error" message="报告读取失败" description={errorText(report.error)} action={<Button onClick={()=>report.refetch()}>重试</Button>}/>}
 {data&&<div key={id}><Space wrap className="spaced-alert"><Tag>{report.data?.schema_version}</Tag><Tag>{data.status==='completed_pending_human'?'自动评审完整，待人工复核':data.status==='incomplete'?'自动评审未完整，请核对失败或缺失样本':data.status}</Tag>{kind==='judge'&&<><Tag>方法版本：{data.method_version??'未记录（旧报告）'}</Tag><Tag>评审范围：{scope}</Tag></>}</Space>
 {kind==='judge'?<JudgeVisualReport key={id} data={data} openDocument={setDocumentId}/>:<GraphVisualReport key={id} data={data} openDocument={setDocumentId}/>}
 <Collapse className="evaluation-card" items={[{key:'method',label:'方法与限制',children:<><p>各报告独立解释，不纳入官方 30 题检索成绩。</p>{data.source_run&&<p>来源报告：<code>{data.source_run}</code></p>}{data.base_results_reused===true?<><b>组合报告：复用基础批次成功项，仅失败项获得额外尝试</b><p>基础状态：{data.base_status??'未记录'}。样本选择与尝试次数存在差异，不能把组合均值当作相同条件下的独立全批结果。</p></>:typeof data.original_results_merged==='boolean'&&<p>{data.original_results_merged?'包含原结果':'原结果未混入本次指标'}</p>}<p>{data.method_note}</p><p>{data.self_evaluation_bias??'Judge 不等于人工真值；同模型自评可能存在偏差。'}</p><p>完成数以通过结构与结果校验的样本为准。HTTP 成功不代表评审合法。失败与 N/A 分开统计。</p><ConfigFacts data={{scope:data.scope,method_version:data.method_version,base_status:data.base_status,note:data.note}}/></>}]}/>
 <AdvancedAudit data={data}/></div>}
 </Modal><DocumentDrawer id={documentId} onClose={()=>setDocumentId(null)}/></>;
}
