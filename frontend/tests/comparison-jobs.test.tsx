import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import EvaluationJobs from '../src/EvaluationJobs';
import Evaluations from '../src/Evaluations';
import { api } from '../src/api';
import type { EvaluationJob, EvaluationDetail, EvaluationOptions, JobEstimate } from '../src/types';
import optionsSnapshot from './fixtures/evaluation-options.json';
vi.mock('../src/Chart',()=>({default:({option,label}:{option:unknown;label:string})=><div role="img" aria-label={label} data-testid="comparison-chart" data-option={JSON.stringify(option)}/>}));
vi.setConfig({testTimeout:40_000});
const options=optionsSnapshot as EvaluationOptions;
const configurations=options.builds.flatMap(b=>options.modes.map(mode=>({build_id:b.build_id,strategy:b.strategy,mode})));
const estimate:JobEstimate={configurations,total_questions:180,logical_calls_max:240,http_attempts_max:720,cost_ceiling_cny:12,conservative_estimate_cny:.7,retries_max:2,candidate_depth:100,rerank_depth:100,top_k:20,query_cache:false,prices_cny_per_million:{embedding:.5},caveats:['TEST ONLY']};
const makeJob=(runs:EvaluationJob['runs']):EvaluationJob=>({job_id:'TEST-COMPARISON-JOB',status:'completed',progress:{completed_questions:180,total_questions:180,completed_runs:runs.filter(r=>r.status==='completed').length,total_runs:6,current_query_id:null},runs,estimate,budget:{logical_calls:240,http_attempts:240,reserved_upper_cny:.5,actual_billed_cny:null,logical_limit:240,http_limit:720,cost_limit_cny:12,pending_request:null},error:null,created_at:'TEST',updated_at:'TEST'});
afterEach(()=>{cleanup();localStorage.clear();history.replaceState(null,'','#evaluations');vi.restoreAllMocks()});
function mount(ui:React.ReactNode){return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}})}><ConfigProvider theme={{token:{motion:false}}} wave={{disabled:true}}>{ui}</ConfigProvider></QueryClientProvider>)}
function mockOptions(value=options){vi.spyOn(api,'evaluationOptions').mockResolvedValue(value);vi.spyOn(api,'evaluationJobs').mockResolvedValue({items:[],total:0})}
it('opens six directly with the matrix, invalidates estimates on profile/build changes, and creates only after explicit confirmation',async()=>{
 mockOptions();const price=vi.spyOn(api,'estimateJob').mockResolvedValue(estimate);const create=vi.spyOn(api,'createJob').mockResolvedValue(makeJob([]));vi.spyOn(api,'evaluationJob').mockResolvedValue(makeJob([]));
 mount(<EvaluationJobs onRun={()=>{}}/>);
 fireEvent.click(screen.getByRole('button',{name:'新建对比实验'}));
 await screen.findByText('六组对比矩阵');
 expect(await screen.findByText(/每组 30 题，共 180 题/)).toBeTruthy();
 expect(screen.getAllByText('dense').length).toBe(2);expect(screen.getAllByText('hybrid_rerank').length).toBe(2);
 await waitFor(()=>expect((screen.getByRole('button',{name:'核对调用与官方单价'}) as HTMLButtonElement).disabled).toBe(false));
 expect(price).not.toHaveBeenCalled();expect(create).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'核对调用与官方单价'}));
 await screen.findByText('我已核对配置和费用上限，明确启动此付费检索实验');
 expect(price.mock.calls[0][0]).toMatchObject({profile:'six',build_ids:Object.fromEntries(options.builds.map(b=>[b.strategy,b.build_id]))});
 const firstKey=price.mock.calls[0][0].idempotency_key;
 expect(screen.getByText('180',{selector:'.ant-descriptions-item-content'})).toBeTruthy();
 fireEvent.click(screen.getByRole('checkbox'));
 fireEvent.click(screen.getByRole('radio',{name:'单配置 · 30 题'}));
 expect(screen.queryByRole('checkbox')).toBeNull();expect((screen.getByRole('button',{name:'创建并启动实验'}) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.click(screen.getByRole('radio',{name:'六组对比 · 180 题'}));
 fireEvent.click(screen.getByRole('button',{name:'核对调用与官方单价'}));
 await screen.findByRole('checkbox');expect(price.mock.calls[1][0].idempotency_key).not.toBe(firstKey);
 fireEvent.click(screen.getByRole('checkbox'));expect(create).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'创建并启动实验'}));
 await waitFor(()=>expect(create).toHaveBeenCalledTimes(1));expect(create.mock.calls[0][0]).toEqual(price.mock.calls[1][0]);
});
it('requires choosing among multiple builds and clears the estimate/confirmation when that choice changes',async()=>{
 const extra={...options.builds.find(b=>b.strategy==='fixed_window')!,build_id:'TEST-OTHER-FIXED'};
 mockOptions({...options,builds:[...options.builds,extra]});const price=vi.spyOn(api,'estimateJob').mockResolvedValue(estimate);const create=vi.spyOn(api,'createJob');
 mount(<EvaluationJobs onRun={()=>{}}/>);fireEvent.click(screen.getByRole('button',{name:'新建对比实验'}));await screen.findByText('待选择就绪构建');
 expect((screen.getByRole('button',{name:'核对调用与官方单价'}) as HTMLButtonElement).disabled).toBe(true);
 const fixed=options.builds.find(b=>b.strategy==='fixed_window')!;
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'固定窗口构建'}));fireEvent.click(await screen.findByText(new RegExp(fixed.build_id),{selector:'.ant-select-item-option-content'}));
 fireEvent.click(screen.getByRole('button',{name:'核对调用与官方单价'}));await screen.findByRole('checkbox');fireEvent.click(screen.getByRole('checkbox'));
 fireEvent.mouseDown(screen.getByRole('combobox',{name:'固定窗口构建'}));fireEvent.click(await screen.findByText(/TEST-OTHER-FIXED/,{selector:'.ant-select-item-option-content'}));
 expect(screen.queryByRole('checkbox')).toBeNull();expect((screen.getByRole('button',{name:'创建并启动实验'}) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.click(screen.getByRole('button',{name:'核对调用与官方单价'}));await screen.findByRole('checkbox');expect(price.mock.calls[1][0]).toMatchObject({build_ids:{fixed_window:'TEST-OTHER-FIXED'}});expect(price.mock.calls[1][0].idempotency_key).not.toBe(price.mock.calls[0][0].idempotency_key);expect(create).not.toHaveBeenCalled();
});
it('disables six when a strategy has no ready build, while keeping the single entry available',async()=>{
 mockOptions({...options,builds:options.builds.filter(b=>b.strategy==='fixed_window')});const price=vi.spyOn(api,'estimateJob');const create=vi.spyOn(api,'createJob');
 mount(<EvaluationJobs onRun={()=>{}}/>);fireEvent.click(screen.getByRole('button',{name:'新建对比实验'}));await screen.findByText('缺少政策层级就绪构建，无法启动六组对比。');
 expect((screen.getByRole('button',{name:'核对调用与官方单价'}) as HTMLButtonElement).disabled).toBe(true);
 fireEvent.click(screen.getByRole('radio',{name:'单配置 · 30 题'}));await waitFor(()=>expect((screen.getByRole('button',{name:'核对调用与官方单价'}) as HTMLButtonElement).disabled).toBe(false));
 expect(price).not.toHaveBeenCalled();expect(create).not.toHaveBeenCalled();
});
it('replaces unrelated selections with only this task completed runs, preserving failed states and single-run details',async()=>{
 const task=makeJob([{run_id:'TEST-A',status:'completed'},{run_id:'TEST-B',status:'completed'},{run_id:'TEST-FAIL',status:'failed'}]);
 mockOptions();vi.mocked(api.evaluationJobs).mockResolvedValue({items:[task],total:1});vi.spyOn(api,'evaluationJob').mockResolvedValue(task);const create=vi.spyOn(api,'createJob');
 vi.spyOn(api,'evaluations').mockResolvedValue({items:['TEST-UNRELATED','TEST-A','TEST-B','TEST-FAIL'].map(run_id=>({run_id,status:run_id==='TEST-FAIL'?'failed':'completed',created_at:'TEST',summary:{}})),total:4});
 vi.spyOn(api,'evaluation').mockImplementation(async id=>({run_id:id,status:id==='TEST-FAIL'?'failed':'completed',config:{strategy:'fixed_window',mode:'dense'},metrics:{'recall@5':id==='TEST-A'?.5:id==='TEST-B'?.75:.1},per_query:[],artifacts:{}} as EvaluationDetail));
 vi.spyOn(api,'extensionReports').mockResolvedValue({items:[],total:0});
 mount(<Evaluations/>);const row=(await screen.findByText('TEST-UNRELATED')).closest('tr')!;fireEvent.click(row.querySelector('input[type=checkbox]')!);await screen.findByTestId('comparison-chart');
 fireEvent.click(screen.getByText('图谱挑战 · 独立题集').closest('[role=tab]')!);
 fireEvent.click(screen.getByText(task.job_id).closest('button')!);fireEvent.click((await screen.findByText('对比本任务结果')).closest('button')!);
 const scope=(await screen.findByText('本任务结果对比')).closest('section')!;expect(within(scope).getByText('TEST-FAIL')).toBeTruthy();expect(within(scope).getByText('排除（未完成 / 失败）')).toBeTruthy();
 expect(screen.getByText('官方 30 题 · 核心检索').closest('[role=tab]')!.getAttribute('aria-selected')).toBe('true');
 await waitFor(()=>expect(JSON.parse(screen.getByTestId('comparison-chart').getAttribute('data-option')!).series[0].data).toEqual([.5,.75]));
 expect((screen.getByText('TEST-UNRELATED').closest('tr')!.querySelector('input[type=checkbox]') as HTMLInputElement).checked).toBe(false);
 fireEvent.click(within(scope).getAllByText('查看详情')[2].closest('button')!);expect(await screen.findByText('实验详情 · TEST-FAIL')).toBeTruthy();expect(create).not.toHaveBeenCalled();
});
it('explains why fewer than two successful task runs cannot be compared',async()=>{
 const task=makeJob([{run_id:'TEST-A',status:'completed'},{run_id:'TEST-FAIL',status:'failed'}]);mockOptions();vi.mocked(api.evaluationJobs).mockResolvedValue({items:[task],total:1});vi.spyOn(api,'evaluationJob').mockResolvedValue(task);const compare=vi.fn();
 mount(<EvaluationJobs onRun={()=>{}} onCompare={compare}/>);fireEvent.click(await screen.findByRole('button',{name:task.job_id}));await screen.findByText(/本任务只有 1 项已完成结果/);expect((screen.getByRole('button',{name:/对比本任务结果/}) as HTMLButtonElement).disabled).toBe(true);expect(compare).not.toHaveBeenCalled();
});
it('hands all six saved completed runs to comparison without creating a new task',async()=>{
 const task=makeJob(configurations.map((c,i)=>({...c,run_id:`TEST-SIX-${i}`,status:'completed'})));mockOptions();vi.mocked(api.evaluationJobs).mockResolvedValue({items:[task],total:1});vi.spyOn(api,'evaluationJob').mockResolvedValue(task);const compare=vi.fn(),create=vi.spyOn(api,'createJob');
 mount(<EvaluationJobs onRun={()=>{}} onCompare={compare}/>);fireEvent.click(await screen.findByRole('button',{name:task.job_id}));await screen.findByText(/将仅选择本任务的 6 项已完成结果/);fireEvent.click(screen.getByRole('button',{name:/对比本任务结果/}));expect(compare).toHaveBeenCalledWith(task);expect(create).not.toHaveBeenCalled();
});
