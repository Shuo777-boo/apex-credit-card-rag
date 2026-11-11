import { Descriptions, Table } from 'antd';
import type { EvaluationJob, ModelPrice, PricingCatalog } from './types';

export const money=(value:number|null|undefined)=>typeof value==='number'&&Number.isFinite(value)?`¥${value.toFixed(6)}`:'未知';
function officialUrl(value:string){try{const url=new URL(value);return url.protocol==='https:'&&url.hostname==='help.aliyun.com'?url.href:undefined}catch{return undefined}}
export function OfficialPricing({value}:{value?:PricingCatalog|null}){
 if(!value)return <p className="muted">官方单价来源待核验；总费用待实际用量确定。</p>;
 return <section aria-label="官方模型单价"><h4>官方模型单价</h4><p>{value.region_name}（{value.region}） · {value.currency} · {value.invocation==='synchronous_online'?'同步在线调用':value.invocation} · 核验日期 {value.verified_on}</p>
  {!value.applicable&&<p role="status">当前调用地域不适用此价格表，费用未知。</p>}
  <Table size="small" rowKey="model" pagination={false} scroll={{x:640}} dataSource={value.models} columns={[
   {title:'模型',dataIndex:'model'},
   {title:'输入单价',render:(_:unknown,r:ModelPrice)=>`${money(r.input_price)} / ${r.unit_tokens.toLocaleString('zh-CN')} Tokens`},
   {title:'输出单价',render:(_:unknown,r:ModelPrice)=>`${money(r.output_price)} / ${r.unit_tokens.toLocaleString('zh-CN')} Tokens`},
   {title:'计费规则与官方来源',render:(_:unknown,r:ModelPrice)=><>{r.billing_rule}<div>{officialUrl(r.source_url)?<a href={officialUrl(r.source_url)} target="_blank" rel="noopener noreferrer">官方价格文档</a>:'官方价格链接待核验'} · {officialUrl(r.protocol_url)?<a href={officialUrl(r.protocol_url)} target="_blank" rel="noopener noreferrer">计量协议</a>:'协议链接待核验'}</div></>}
  ]}/><p className="muted">公开单价按实际 Token 用量折算；任务总费用待实际用量确定，实际结算以供应商账单为准。</p>
 </section>;
}
export function UsageCharges({job}:{job:EvaluationJob}){
 const usage=job.usage_cost,pricing=job.pricing??job.estimate.pricing;
 const applicable=!!pricing?.applicable;
 const total=applicable&&usage?.status==='complete'?usage.total_usage_cost_cny:null;
 const known=applicable?usage?.known_usage_cost_cny:null;
 return <section aria-label="实际用量费用"><h4>实际用量与费用</h4>
  {usage?.valuation_basis==='current_verified_prices_for_legacy_usage'&&<p>历史任务按当前核验单价折算，不代表执行当时费率。</p>}
  <Descriptions size="small" column={2} items={[
   {key:'total',label:'任务用量折算总费用',children:money(total)},
   {key:'known',label:'已报告用量部分费用（非任务总额）',children:money(known)},
   {key:'coverage',label:'用量覆盖',children:usage?`${usage.status==='complete'?'完整':usage.status==='partial'?'部分':'未知'} · 已记录 ${usage.known_calls} 次 / 未覆盖 ${usage.unknown_calls} 次`:'未知（历史任务缺少用量记录）'},
   {key:'billed',label:'供应商实际账单',children:money(usage?.actual_billed_cny??job.budget.actual_billed_cny)},
  ]}/>{usage&&<p className="muted">{Object.entries(usage.known_tokens_by_model).map(([model,tokens])=>`${model}：${tokens.toLocaleString('zh-CN')} Tokens`).join('；')||'尚无可核算的 Token 用量。'}</p>}
  <p className="muted">仅真实供应商 usage 按官方单价折算；缺失记录、失败或重试未报告用量均不能记为免费。{usage?.notes.join(' ')}</p>
 </section>;
}
