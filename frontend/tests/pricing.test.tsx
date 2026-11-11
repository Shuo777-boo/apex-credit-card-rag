import { afterEach, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { OfficialPricing, UsageCharges } from '../src/Pricing';
import type { EvaluationJob, PricingCatalog, UsageCost } from '../src/types';
// Semantic test data only. Production prices always come from the backend catalog.
const pricing:PricingCatalog={version:'TEST',verified_on:'2026-11-10',region:'cn-beijing',region_name:'中国内地 · 华北2（北京）',currency:'CNY',invocation:'synchronous_online',applicable:true,notes:[],models:[{model:'text-embedding-v4',input_price:.5,unit_tokens:1000000,output_price:0,billing_rule:'按真实输入 Token 计费',source_url:'https://help.aliyun.com/zh/model-studio/model-pricing',protocol_url:'https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api',source_sha256:'TEST',protocol_sha256:'TEST'}]};
const usage:UsageCost={status:'partial',known_usage_cost_cny:.001,total_usage_cost_cny:null,actual_billed_cny:null,known_tokens_by_model:{'text-embedding-v4':2000},known_calls:1,unknown_calls:1,valuation_basis:'current_verified_prices_for_legacy_usage',notes:[]};
const job=(value?:UsageCost,catalog:PricingCatalog|undefined=pricing)=>({pricing:catalog,usage_cost:value,estimate:{},budget:{actual_billed_cny:null}} as EvaluationJob);
afterEach(cleanup);
it('shows units, region, verification date and clickable official sources without quoting a task total',()=>{
 render(<OfficialPricing value={pricing}/>);
 expect(screen.getByText(/cn-beijing.*2026-11-10/)).toBeTruthy();
 expect(screen.getByText('¥0.500000 / 1,000,000 Tokens')).toBeTruthy();
 expect(screen.getByRole('link',{name:'官方价格文档'}).getAttribute('href')).toBe(pricing.models[0].source_url);
 expect(screen.getByText(/任务总费用待实际用量确定/)).toBeTruthy();
});
it('does not upgrade old numeric prices to verified official pricing or missing usage to zero',()=>{
 render(<><OfficialPricing/><UsageCharges job={job()}/></>);
 expect(screen.getByText(/官方单价来源待核验/)).toBeTruthy();
 expect(screen.getByText(/历史任务缺少用量记录/)).toBeTruthy();
 expect(screen.queryByText(/¥0/)).toBeNull();
});
it('keeps partial usage separate from unknown total and labels historical repricing',()=>{
 render(<UsageCharges job={job(usage)}/>);
 expect(screen.getByText(/历史任务按当前核验单价折算/)).toBeTruthy();
 expect(screen.getByText('¥0.001000')).toBeTruthy();
 expect(screen.getByText(/已记录 1 次 \/ 未覆盖 1 次/)).toBeTruthy();
 expect(screen.getAllByText('未知')).toHaveLength(2);
});
it('allows an explicitly measured zero total but never values an inapplicable region',()=>{
 const complete={...usage,status:'complete' as const,known_usage_cost_cny:0,total_usage_cost_cny:0,unknown_calls:0};
 const view=render(<UsageCharges job={job(complete)}/>);
 expect(screen.getAllByText('¥0.000000')).toHaveLength(2);
 view.rerender(<UsageCharges job={job(complete,{...pricing,applicable:false})}/>);
 expect(screen.queryByText(/¥0/)).toBeNull();
});
it('does not execute or link untrusted source URL schemes',()=>{
 render(<OfficialPricing value={{...pricing,models:[{...pricing.models[0],source_url:'javascript:alert(1)',protocol_url:'https://example.com/price'}]}}/>);
 expect(screen.queryByRole('link')).toBeNull();
 expect(screen.getByText(/官方价格链接待核验/)).toBeTruthy();
});
