import type { AnswerResponse, Mode, SearchResponse } from './types';
export interface Turn { id:string; question:string; mode:Mode; tier:string|null; retrievalOnly:boolean; state:'pending'|'completed'|'failed'|'interrupted'; answer?:AnswerResponse; retrieval?:SearchResponse; error?:string; createdAt:string }
export interface Session { id:string; title:string; updatedAt:string; turns:Turn[] }
export const CHAT_KEY='apex-chat-v2';
export const newSession=():Session=>({id:crypto.randomUUID(),title:'新对话',updatedAt:new Date().toISOString(),turns:[]});
export function loadSessions():Session[]{
  const raw=localStorage.getItem(CHAT_KEY); if(!raw)return [newSession()];
  const data:unknown=JSON.parse(raw);
  if(!Array.isArray(data)||!data.every(s=>s&&typeof s.id==='string'&&typeof s.title==='string'&&Array.isArray(s.turns)&&s.turns.every((t:Turn)=>typeof t.id==='string'&&typeof t.question==='string'&&['pending','completed','failed','interrupted'].includes(t.state))))throw new Error('会话存档无法读取，请先导出或清除站点存储。');
  return data.length?data.map((s:Session)=>({...s,turns:s.turns.map(t=>t.state==='pending'?{...t,state:'interrupted',error:'页面刷新中断了等待。后端请求可能仍在执行并计费；不会自动重新发送。'}:t)})):[newSession()];
}
export function boundedHistory(turns:Turn[]){
  const pairs:{role:'user'|'assistant';content:string}[][]=[];let chars=0;
  for(const t of [...turns].reverse()){
    if(t.state!=='completed'||!t.answer)break;
    const q=t.question.trim(),a=t.answer.answer.trim();
    if(q.length>4000||a.length>4000||!q||!a)break;
    if(chars+q.length+a.length>12000||pairs.length===3)break;
    pairs.unshift([{role:'user',content:q},{role:'assistant',content:a}]); chars+=q.length+a.length;
  }
  return pairs.flat();
}
