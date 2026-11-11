import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ExtensionReports from '../src/ExtensionReports';
import Evaluations from '../src/Evaluations';
import { api } from '../src/api';
import finalReport from './fixtures/judge-final.json';
import oldReport from './fixtures/judge-old.json';
import graphReport from './fixtures/graph.json';
import coreReport from './fixtures/core.json';
import type { ExtensionReport, EvaluationDetail } from '../src/types';
import { QualityMetrics } from '../src/ReportVisuals';
vi.mock('../src/Chart',()=>({default:({label,option}:{label:string;option:unknown})=><div role="img" aria-label={label} data-option={JSON.stringify(option)}/>}));
// Real report fixtures render many evidence/claim cards; keep an explicit bound.
vi.setConfig({testTimeout:45_000});
afterEach(()=>{cleanup();vi.restoreAllMocks();localStorage.clear();history.replaceState(null,'','#evaluations')});
function mount(ui:React.ReactNode){return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}})}>{ui}</QueryClientProvider>)}
// rc-util deliberately returns the same 'test-id' for every dialog title in NODE_ENV=test.
// For nested dialogs, locate the actual heading's dialog ancestor instead of its colliding aria-labelledby.
function noPaid(){return [vi.spyOn(api,'createJob'),vi.spyOn(api,'estimateJob'),vi.spyOn(api,'answer'),vi.spyOn(api,'search')]}
function reports(kind:'graph'|'judge'){
 const rows=kind==='judge'?[finalReport,oldReport]:[graphReport];
 vi.spyOn(api,'extensionReports').mockResolvedValue({items:rows.map(r=>({report_id:r.report_id,kind,status:r.data.status,metrics:r.data.metrics,schema_version:r.schema_version})),total:rows.length});
 return vi.spyOn(api,'extensionReport').mockImplementation(async id=>rows.find(r=>r.report_id===id) as unknown as ExtensionReport);
}
it('reads real final and failed old Judge reports only inside dialogs; nulls, bars and quoted evidence remain faithful',async()=>{
 const paid=noPaid();reports('judge');mount(<ExtensionReports kind="judge"/>);
 const row=await screen.findByRole('row',{name:new RegExp(finalReport.report_id)});
 expect(screen.queryByText('逐样本 · 回答 → 证据 → 主张 → 人工复核')).toBeNull();
 fireEvent.click(within(row).getByRole('button',{name:'查看报告'}));
 const dialog=await screen.findByRole('dialog');
 const chart=await within(dialog).findByRole('img',{name:'Judge 相关性与忠实度均值，与指标表同源'});
 const values=JSON.parse(chart.getAttribute('data-option')!);
 expect(values.series[0].data).toEqual(Object.values(finalReport.data.metrics).map(v=>v.context_relevance.mean));
 expect(within(dialog).getByText('0.4267 / 30 / 0')).toBeTruthy();
 expect(within(dialog).getByText('人工复核：0 / 10')).toBeTruthy();
 const verdict=finalReport.data.verdicts.find(v=>v.sample_id===finalReport.data.samples[0].sample_id)!;
 expect(within(dialog).getByText(new RegExp(`相关性 ${verdict.context_relevance!.toFixed(4)}`))).toBeTruthy();
 const quotes=verdict.verdict!.claims.flatMap(c=>c.support_quotes??[]);
 expect(quotes.length).toBeGreaterThan(1);
 quotes.forEach(quote=>expect(dialog.textContent).toContain(quote.quote));
 expect(within(dialog).queryByText('选择审计字段')).toBeNull();
 fireEvent.mouseDown(within(dialog).getByRole('combobox',{name:'切换报告'}));
 fireEvent.click(await screen.findByText(oldReport.report_id,{selector:'.ant-select-item-option-content'}));
 await within(dialog).findByText('该样本评审失败或缺失，保留回答，不补造评分');
 expect(within(dialog).getByText(/相关性 N\/A · 忠实度 N\/A/)).toBeTruthy();
 expect(within(dialog).queryByText('0.4267 / 30 / 0')).toBeNull();
 fireEvent.click(within(dialog).getByRole('button',{name:'关闭报告'}));
 await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
 paid.forEach(spy=>expect(spy).not.toHaveBeenCalled());
});
it('shows real graph coverage and missing paths without inventing edges, and opens the matching source',async()=>{
 const paid=noPaid();reports('graph');
 vi.spyOn(api,'document').mockImplementation(async id=>({doc_id:id,title:'源政策',content:'完整源文本',category:'Fees',card_tier:'Apex Platinum',effective_year:2025}));
 mount(<ExtensionReports kind="graph"/>);
 fireEvent.click(within(await screen.findByRole('row',{name:new RegExp(graphReport.report_id)})).getByRole('button',{name:'查看报告'}));
 const dialog=await screen.findByRole('dialog');
 const chart=await within(dialog).findByRole('img',{name:'图谱文档和引用覆盖率，与指标表同源'});
 expect(JSON.parse(chart.getAttribute('data-option')!).series[0].data).toEqual(Object.values(graphReport.data.metrics).map(v=>v.document_coverage));
 expect(within(dialog).getByText('本次没有返回图扩展路径')).toBeTruthy();
 expect(within(dialog).getByText('人工复核已记录 0 / 2 项')).toBeTruthy();
 const doc=graphReport.data.samples[0].required_documents[0];
 fireEvent.click(within(dialog).getAllByRole('button',{name:doc,exact:true})[0]);
 expect(await screen.findByText('完整源文本')).toBeTruthy();
 expect(screen.getByText('政策原文 · 来源核验').closest('[role=dialog]')).toBeTruthy();
 expect(api.document).toHaveBeenCalledWith(doc,expect.any(AbortSignal));
 fireEvent.click(screen.getByText('政策原文 · 来源核验').closest('[role=dialog]')!.querySelector('button')!);
 fireEvent.click(within(dialog).getAllByRole('button',{name:'阅读该题图谱结果',exact:true}).find(b=>!(b as HTMLButtonElement).disabled)!);
 const edge=graphReport.data.samples[1].response.retrieval.graph_paths[0].edges[0];
 expect(await within(dialog).findByText(edge.evidence)).toBeTruthy();
 fireEvent.click(within(dialog).getAllByRole('button',{name:new RegExp(`${edge.doc_id} · 来源原文`)})[0]);
 await waitFor(()=>expect(api.document).toHaveBeenCalledWith(edge.doc_id,expect.any(AbortSignal)));
 paid.forEach(spy=>expect(spy).not.toHaveBeenCalled());
});
it('keeps absent quality metrics null in the chart and N/A in its table',()=>{
 mount(<QualityMetrics data={{'recall@5':null,'precision@5':.25}}/>);
 const chart=screen.getByRole('img',{name:'检索质量指标原值'});
 expect(JSON.parse(chart.getAttribute('data-option')!).series[0].data).toEqual([null,.25]);
 expect(screen.getByText('N/A')).toBeTruthy();expect(screen.getByText('0.2500')).toBeTruthy();
});
it('handles report failure/retry and drops earlier content during a pending switch',async()=>{
 reports('judge');const read=vi.mocked(api.extensionReport).mockRejectedValueOnce(new Error('TEST unavailable')).mockResolvedValueOnce(finalReport as unknown as ExtensionReport);
 mount(<ExtensionReports kind="judge"/>);
 fireEvent.click(within(await screen.findByRole('row',{name:new RegExp(finalReport.report_id)})).getByRole('button',{name:'查看报告'}));
 const dialog=await screen.findByRole('dialog');await within(dialog).findByText('报告读取失败');
 fireEvent.click(within(dialog).getByRole('button',{name:/重\s*试/}));
 await within(dialog).findByRole('img',{name:'Judge 相关性与忠实度均值，与指标表同源'});
 read.mockImplementation(()=>new Promise(()=>{}));
 fireEvent.mouseDown(within(dialog).getByRole('combobox',{name:'切换报告'}));
 fireEvent.click(await screen.findByText(oldReport.report_id,{selector:'.ant-select-item-option-content'}));
 await waitFor(()=>expect(within(dialog).queryByRole('img',{name:'Judge 相关性与忠实度均值，与指标表同源'})).toBeNull());
 fireEvent.click(within(dialog).getByRole('button',{name:'关闭报告'}));
 await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
});
it('opens official hash links and resets nested question/source state when the run changes',async()=>{
 const paid=noPaid();vi.spyOn(api,'evaluationOptions').mockRejectedValue(new Error('read only'));vi.spyOn(api,'evaluationJobs').mockResolvedValue({items:[],total:0});
 vi.spyOn(api,'evaluations').mockResolvedValue({items:[],total:0});
 const next={...coreReport,run_id:'next-real-shape',per_query:[]};
 vi.spyOn(api,'evaluation').mockImplementation(async id=>(id==='next-real-shape'?next:coreReport) as unknown as EvaluationDetail);
 history.replaceState(null,'',`#evaluations?run=${coreReport.run_id}`);mount(<Evaluations/>);
 const dialog=await screen.findByRole('dialog',{name:new RegExp(`实验详情 · ${coreReport.run_id}`)});
 fireEvent.click((await within(dialog).findAllByRole('button',{name:'查看题目详情'}))[0]);
 expect((await screen.findByText(`题目详情 · ${coreReport.per_query[0].query_id}`)).closest('[role=dialog]')).toBeTruthy();
 history.replaceState(null,'','#evaluations?run=next-real-shape');fireEvent(window,new HashChangeEvent('hashchange'));
 await screen.findByRole('dialog',{name:'实验详情 · next-real-shape'});
 await waitFor(()=>expect(screen.queryByText(`题目详情 · ${coreReport.per_query[0].query_id}`)).toBeNull());
 expect(screen.queryByText('同口径指标比较')).toBeNull();
 paid.forEach(spy=>expect(spy).not.toHaveBeenCalled());
});
