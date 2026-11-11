import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Empty, Input, Modal, Select, Space, Spin, Tag, Tooltip } from 'antd';
import { DeleteOutlined, MessageOutlined, PlusOutlined, SendOutlined, StopOutlined } from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { api, ApiError, errorText, formatMs } from './api';
import type { Mode } from './types';
import { validCitations } from './result-utils';
import { boundedHistory, CHAT_KEY, loadSessions, newSession, type Session, type Turn } from './chat-store';
import AnswerMarkdown from './AnswerMarkdown';
import RetrievalDetails from './RetrievalDetails';
import DocumentDrawer from './DocumentDrawer';
const examples=['What is the balance transfer fee for Apex Platinum?','How do I report a lost or stolen card?','白金卡的年费是多少？'];
// Hide only the requested presentation notice; retain every other backend warning.
const visibleChatWarning=(warning:string)=>warning.trim()!=='引用编号检查仅验证编号映射，不代表事实忠实度已经得到证明。';
export default function Workbench(){
  const [initial]=useState(()=>{try{return {sessions:loadSessions(),error:''}}catch(e){return {sessions:[newSession()],error:errorText(e)}}});
  const [sessions,setSessions]=useState<Session[]>(initial.sessions);
  const [active,setActive]=useState(initial.sessions[0].id);
  const [storageError,setStorageError]=useState(initial.error);
  const [question,setQuestion]=useState(''); const [mode,setMode]=useState<Mode>('hybrid_rerank');
  const [tier,setTier]=useState<string|null>(null); const [retrievalOnly,setRetrievalOnly]=useState(false);
  const [documentId,setDocumentId]=useState<string|null>(null); const [detail,setDetail]=useState<Turn|null>(null);
  const [mobileSessions,setMobileSessions]=useState(false); const [pending,setPending]=useState(false);const [slow,setSlow]=useState(false);
  const flight=useRef<{controller:AbortController;sessionId:string;turnId:string}|null>(null);
  const log=useRef<HTMLDivElement>(null);
  const capability=useQuery({queryKey:['capabilities'],queryFn:({signal})=>api.capabilities(signal)});
  const readiness=useQuery({queryKey:['readiness'],queryFn:({signal})=>api.readiness(signal),refetchInterval:30000});
  const graphReady=capability.data?.graph.available===true&&readiness.data?.status==='ready'&&!readiness.error;
  const current=sessions.find(s=>s.id===active)??sessions[0];
  useEffect(()=>{if(initial.error)return;try{localStorage.setItem(CHAT_KEY,JSON.stringify(sessions));setStorageError('')}catch{setStorageError('浏览器存储已满或不可用，本次变更尚未保存。请删除不需要的会话后重试；刷新可能丢失未保存消息。')}},[sessions,initial.error]);
  useEffect(()=>{if(!pending){setSlow(false);return}const timer=setTimeout(()=>setSlow(true),12000);return()=>clearTimeout(timer)},[pending]);
  useEffect(()=>()=>flight.current?.controller.abort(),[]);
  useEffect(()=>{log.current?.scrollTo?.({top:log.current.scrollHeight,behavior:'smooth'})},[current.turns.length,pending]);
  const patch=(sessionId:string,turnId:string,values:Partial<Turn>)=>setSessions(items=>items.map(s=>s.id===sessionId?{...s,updatedAt:new Date().toISOString(),turns:s.turns.map(t=>t.id===turnId?{...t,...values}:t)}:s));
  const submit=async()=>{
    if(flight.current||!question.trim()||question.trim().length>4000)return;
    const controller=new AbortController(),sessionId=current.id,turnId=crypto.randomUUID();
    const job={controller,sessionId,turnId};flight.current=job;setPending(true);
    const text=question.trim(); const turn:Turn={id:turnId,question:text,mode,tier,retrievalOnly,state:'pending',createdAt:new Date().toISOString()};
    const history=boundedHistory(current.turns);
    setSessions(items=>items.map(s=>s.id===sessionId?{...s,title:s.turns.length?s.title:text.slice(0,32),turns:[...s.turns,turn],updatedAt:turn.createdAt}:s));setQuestion('');
    const body={query:text,mode,top_k:5,card_tier:tier};
    try{
      if(retrievalOnly){const retrieval=await api.search(body,controller.signal);if(flight.current===job)patch(sessionId,turnId,{state:'completed',retrieval})}
      else{const answer=await api.answer({...body,history},controller.signal);if(flight.current===job)patch(sessionId,turnId,{state:'completed',answer,retrieval:answer.retrieval})}
    }catch(e){if(flight.current===job){const partial=e instanceof ApiError?e.body.error.details?.retrieval:undefined;patch(sessionId,turnId,{state:'failed',error:errorText(e),retrieval:partial})}}
    finally{if(flight.current===job){flight.current=null;setPending(false)}}
  };
  const stop=()=>{const job=flight.current;if(!job)return;flight.current=null;job.controller.abort();patch(job.sessionId,job.turnId,{state:'interrupted',error:'已停止浏览器等待。后端和供应商请求可能仍在执行并计费；此操作不代表后端任务已取消。'});setPending(false)};
  const create=()=>{const s=newSession();setSessions(items=>[s,...items]);setActive(s.id);setQuestion('');setMobileSessions(false)};
  const remove=(s:Session)=>Modal.confirm({title:'删除此本地会话？',content:'将删除此浏览器保存的消息与证据，不会取消已提交的后端请求。',okText:'删除',cancelText:'保留',okButtonProps:{danger:true},onOk:()=>{setSessions(items=>{const rest=items.filter(i=>i.id!==s.id);if(!rest.length)rest.push(newSession());if(active===s.id)setActive(rest[0].id);return rest})}});
  const sessionList=<><Button block type="primary" aria-label="新建对话" icon={<PlusOutlined/>} onClick={create}>新建对话</Button><div className="session-list">{sessions.map(s=><div className={`session-row ${s.id===current.id?'selected':''}`} key={s.id}><button onClick={()=>{setActive(s.id);setQuestion('');setMobileSessions(false)}}><MessageOutlined/><span>{s.title}<small>{s.turns.length} 轮 · {new Date(s.updatedAt).toLocaleDateString('zh-CN')}</small></span></button><Tooltip title="删除会话"><Button type="text" size="small" aria-label={`删除会话 ${s.title}`} icon={<DeleteOutlined/>} onClick={()=>remove(s)}/></Tooltip></div>)}</div><p className="muted session-note">会话与政策证据仅保存在此浏览器，请勿输入个人敏感信息。</p></>;
  return <><div className="chat-workspace"><aside className="chat-sessions">{sessionList}</aside><section className="chat-main"><header className="chat-header"><div><h1>{current.title}</h1><small>最多 3 轮有界历史 · 每轮独立政策证据</small></div><Button className="mobile-session-button" onClick={()=>setMobileSessions(true)}>会话列表</Button></header>
    {storageError&&<Alert type="warning" showIcon message={storageError}/>}
    <div className="chat-log" ref={log} role="log" aria-label="对话消息">
      {!current.turns.length&&<div className="chat-empty"><span className="chat-avatar"><MessageOutlined/></span><h2>今天需要了解什么政策？</h2><p>从英文政策中检索证据，逐轮查看引用与检索过程。</p><div className="chat-examples">{examples.map(q=><Button key={q} onClick={()=>setQuestion(q)}>{q}</Button>)}</div><p className="muted">中文提问用于演示，不计入官方 30 题成绩。</p></div>}
      {current.turns.map(t=>{const answer=t.answer,citations=answer?validCitations(answer):[];return <article className="chat-turn" key={t.id}><div className="user-message"><div>{t.question}</div><small>{t.mode}{t.tier?` · ${t.tier}`:''}{t.retrievalOnly?' · 仅检索':''}</small></div><div className="assistant-message"><div className="assistant-label"><span className="chat-avatar">A</span><b>Apex 知识助手</b></div>
        {t.state==='pending'?<div role="status"><Spin size="small"/> {slow?'请求耗时较长，正在等待后端真实结果…':'正在检索政策并生成回答…'}</div>:null}
        {t.error&&<Alert type={t.state==='interrupted'?'warning':'error'} showIcon message={t.retrieval?'生成失败，已保留成功检索到的证据':t.state==='interrupted'?'等待已中断':'本轮请求失败'} description={t.error}/>}
        {answer&&<><AnswerMarkdown answer={answer.answer} citations={citations} open={setDocumentId}/>{answer.evidence_insufficient&&<Alert showIcon type="warning" message="证据不足，请补充问题或核对政策适用范围"/>}{(!answer.citation_valid||citations.length<answer.citations.length)&&<Alert type="warning" message="存在未通过本轮证据校验的引用，已禁用相关链接"/>}{answer.warnings?.filter(visibleChatWarning).map((w,i)=><Alert key={i} type="warning" message={w}/>)}
          <div className="citation-list">{citations.map(c=><Button key={c.id} size="small" onClick={()=>setDocumentId(c.doc_id)}>[{c.id}] {c.doc_id}</Button>)}</div>
          <div className="context-note">{answer.context?.mode==='bounded_history_rewrite'?<>使用 {answer.context.history_messages_used} 条历史消息 · 实际检索问题：{answer.context.retrieval_query}</>:'本轮使用独立问题检索'}<br/>历史回答不作为政策证据。</div></>}
        {t.retrieval&&<><div className="turn-metadata"><Tag>{t.retrieval.results.length} 篇证据</Tag><span>检索 {formatMs(t.retrieval.timings_ms.retrieval_total)}</span><span>问答 {formatMs(answer?.timings_ms.answer_total)}</span><Button size="small" onClick={()=>setDetail(t)}>查看本轮证据与检索过程</Button></div>{!t.retrieval.results.length&&<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的政策证据"/>}{t.retrieval.warnings?.filter(visibleChatWarning).map((w,i)=><Alert key={i} type="warning" message={w}/>)}</>}
      </div></article>})}
    </div><div className="chat-composer"><Space wrap><Select aria-label="检索模式" value={mode} onChange={setMode} options={[{value:'dense',label:'Dense 向量'},{value:'hybrid',label:'Hybrid 混合'},{value:'hybrid_rerank',label:'Hybrid + Rerank'},{value:'graph_enhanced',label:'Graph 图扩展',disabled:!graphReady}]}/><Select aria-label="卡种筛选" value={tier??"all"} onChange={v=>setTier(v==="all"?null:v)} style={{minWidth:150}} options={[{value:'all',label:'全部卡种'},...['Apex Platinum','Apex Preferred Travel','Apex Everyday Freedom','Apex Business Select'].map(value=>({value,label:value}))]}/><Button type={retrievalOnly?'primary':'text'} onClick={()=>setRetrievalOnly(!retrievalOnly)}>{retrievalOnly?'仅检索证据':'检索并回答'}</Button></Space>
      <Input.TextArea aria-label="你想了解什么政策？" placeholder="输入政策问题，或继续追问…" value={question} maxLength={4000} onChange={e=>setQuestion(e.target.value)} autoSize={{minRows:2,maxRows:5}} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void submit()}}}/><div className="composer-bottom"><small>Enter 发送 · Shift + Enter 换行 · 本次历史 {retrievalOnly?0:boundedHistory(current.turns).length} 条（遇失败、仅检索或超长轮即停止向前选取） · 追问增加一次改写调用</small>{pending?<Button aria-label="停止等待" icon={<StopOutlined/>} onClick={stop}>停止等待</Button>:<Button type="primary" aria-label="发送消息" icon={<SendOutlined/>} disabled={!question.trim()} onClick={()=>void submit()}>发送消息</Button>}</div>
    </div></section></div><Drawer title="会话列表" open={mobileSessions} onClose={()=>setMobileSessions(false)} placement="left">{sessionList}</Drawer><Drawer title="本轮检索与证据" width="min(1100px, 100vw)" open={!!detail} onClose={()=>setDetail(null)} destroyOnHidden>{detail?.retrieval&&<RetrievalDetails retrieval={detail.retrieval} timings={detail.answer?.timings_ms??detail.retrieval.timings_ms}/>}</Drawer><DocumentDrawer id={documentId} onClose={()=>setDocumentId(null)}/></>;
}


