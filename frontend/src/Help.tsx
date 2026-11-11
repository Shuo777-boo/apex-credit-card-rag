import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Empty, Input, Modal, Space, Tag } from 'antd';
import { DownloadOutlined, SearchOutlined } from '@ant-design/icons';
import { guide, matchesSection, type GuideImage, type GuideSection } from './help/model';
import './help/help.css';
const params=()=>new URLSearchParams(location.hash.split('?')[1]??'');
export default function Help(){
  const [query,setQuery]=useState(()=>params().get('q')??'');
  const [active,setActive]=useState(()=>params().get('section')??guide.sections[0].id);
  const [preview,setPreview]=useState<GuideImage>();
  const [downloadError,setDownloadError]=useState('');
  const [downloading,setDownloading]=useState('');
  const visible=useMemo(()=>guide.sections.filter(s=>matchesSection(s,query)),[query]);
  useEffect(()=>{
    if(!preview)return;
    const close=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();setPreview(undefined)}};
    window.addEventListener('keydown',close);return()=>window.removeEventListener('keydown',close);
  },[preview]);
  useEffect(()=>{
    const update=()=>{setQuery(params().get('q')??'');setActive(params().get('section')??guide.sections[0].id)};
    window.addEventListener('hashchange',update);return()=>window.removeEventListener('hashchange',update);
  },[]);
  useEffect(()=>{
    if(!params().get('section'))return;
    const timer=setTimeout(()=>{const target=document.getElementById(`guide-${active}`);target?.scrollIntoView({block:'start'});target?.focus({preventScroll:true})},0);
    return()=>clearTimeout(timer);
  },[active,query]);
  const search=(value:string)=>{setQuery(value);const p=new URLSearchParams();if(value)p.set('q',value);history.replaceState(null,'',`#help${p.size?'?'+p:''}`)};
  const jump=(section:string)=>{const p=new URLSearchParams({section});if(query)p.set('q',query);location.hash=`help?${p}`;if(active===section){const node=document.getElementById(`guide-${section}`);node?.scrollIntoView({block:'start'});node?.focus({preventScroll:true})}};
  const download=async(kind:'html'|'md')=>{
    setDownloadError('');setDownloading(kind);
    try{const response=await fetch(`/help/apex-guide.${kind}`);if(!response.ok)throw new Error(`HTTP ${response.status}`);
      const blob=await response.blob();const url=URL.createObjectURL(blob);const anchor=document.createElement('a');anchor.href=url;anchor.download=`Apex-使用指南.${kind}`;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch{setDownloadError('说明书下载失败，请检查网络或浏览器下载权限后重试。维护者请确认已生成 /help/apex-guide.html 和 .md。')}finally{setDownloading('')}
  };
  return <div className="help-page">
    <section className="help-hero"><div className="eyebrow">APEX / GETTING STARTED</div><Tag color="blue">从提问到核验</Tag><h1>{guide.title}</h1><p>{guide.intro}</p><Space wrap><Button type="primary" onClick={()=>{search('');jump('quickstart')}}>开始首次问答</Button><Button aria-label="下载离线 HTML" icon={<DownloadOutlined/>} loading={downloading==='html'} disabled={!!downloading} onClick={()=>void download('html')}>下载离线 HTML</Button><Button aria-label="下载 Markdown" icon={<DownloadOutlined/>} loading={downloading==='md'} disabled={!!downloading} onClick={()=>void download('md')}>下载 Markdown</Button></Space><small>内容核对：{guide.version} · HTML 内嵌图片，可断网阅读</small></section>
    {downloadError&&<Alert type="error" showIcon message={downloadError} closable onClose={()=>setDownloadError('')}/>}
    <div className="help-search"><label htmlFor="help-search">搜索使用指南</label><Input id="help-search" prefix={<SearchOutlined/>} allowClear value={query} onChange={e=>search(e.target.value)} placeholder="搜索标题、正文、步骤或常见问题，如：卡种、费用、存储"/><span role="status" aria-live="polite">{query?`找到 ${visible.length} 个相关章节`:`共 ${guide.sections.length} 章 · 可按顺序阅读`}</span></div>
    <div className="help-layout"><aside className="help-toc"><nav aria-label="指南章节目录"><b>本页目录</b>{visible.map(s=><a key={s.id} href={`#help?${new URLSearchParams({section:s.id,...(query?{q:query}:{})})}`} aria-label={s.title} aria-current={active===s.id?'location':undefined} onClick={e=>{e.preventDefault();jump(s.id)}}><span className="help-toc-number" aria-hidden="true">{s.title.split(' · ')[0]}</span><span className="help-toc-title">{s.title.split(' · ').slice(1).join(' · ')}</span></a>)}</nav></aside>
    <div className="help-content"><Alert className="help-demo-note" type="info" showIcon message="一套示例，贯穿操作全过程" description={guide.example}/>
    {!visible.length&&<div className="help-empty"><Empty description={`没有找到与「${query}」相关的内容`}/><Button onClick={()=>search('')}>清除搜索，查看全部章节</Button></div>}
    {visible.map(s=><GuideChapter key={s.id} section={s} searching={!!query.trim()} preview={setPreview}/>)}
    </div></div>
    <Modal title={preview?.caption??'图片预览'} open={!!preview} onCancel={()=>setPreview(undefined)} footer={<Button onClick={()=>setPreview(undefined)}>关闭图片</Button>} width="min(1500px, 96vw)" className="help-image-modal" destroyOnHidden keyboard focusTriggerAfterClose><p>按 Escape 或点击关闭退出。可在图片区域滚动查看原图。</p>{preview&&<div className="help-image-scroll"><img src={`/help/images/${preview.file}`} alt={preview.alt}/></div>}</Modal>
  </div>;
}
function GuideChapter({section:s,searching,preview}:{section:GuideSection;searching:boolean;preview:(i:GuideImage)=>void}){
  return <section className="help-chapter" id={`guide-${s.id}`} tabIndex={-1} aria-labelledby={`guide-title-${s.id}`}><div className="help-chapter-heading"><h2 id={`guide-title-${s.id}`}>{s.title}</h2><p>{s.summary}</p></div>
    {s.goal&&<dl className="help-facts"><dt>操作目标</dt><dd>{s.goal}</dd><dt>适用与准备</dt><dd>{s.audience}</dd><dt>菜单路径</dt><dd>{s.path}</dd></dl>}
    {s.paragraphs?.map((p,i)=><p key={i}>{p}</p>)}
    {s.fields&&<><h3>字段怎么填</h3><div className="help-table-scroll" tabIndex={0} role="region" aria-label={`${s.title}字段说明，可横向滚动`}><table><thead><tr><th>字段</th><th>是否必填 / 默认</th><th>示例值</th><th>填写建议与限制</th></tr></thead><tbody>{s.fields.map(f=><tr key={f.name}><th scope="row">{f.name}</th><td>{f.required}</td><td>{f.example}</td><td>{f.advice}</td></tr>)}</tbody></table></div></>}
    {s.steps&&<><h3>按顺序操作</h3><ol className="help-steps">{s.steps.map((step,i)=><li key={i}><p>{step.action}</p><div className="help-expected"><b>预期结果</b><span>{step.expected}</span></div></li>)}</ol></>}
    {s.images?.map(i=><figure key={i.file}><button className="help-image-button" aria-label={`放大图片：${i.caption}`} onClick={()=>preview(i)}><img src={`/help/images/${i.file}`} alt={i.alt} loading="lazy" decoding="async" width={1440} height={1000}/><span>点击放大 · 支持键盘 Enter</span></button><figcaption>{i.caption}</figcaption></figure>)}
    {s.checks&&<div className="help-checks"><h3>完成后检查</h3><ul>{s.checks.map(p=><li key={p}>{p}</li>)}</ul></div>}
    {s.errors&&<><h3>遇到问题</h3>{s.errors.map(e=><div className="help-trouble" key={e.problem}><h4>{e.problem}</h4><p>{e.solution}</p></div>)}</>}
    {s.faqs?.map((f,i)=><details className="help-faq" key={`${f.question}-${searching}`} open={searching||undefined}><summary>{i+1}. {f.question}</summary><p>{f.answer}</p></details>)}
    {s.advanced&&<details className="help-advanced" key={String(searching)} open={searching||undefined}><summary>{s.advanced.title}</summary>{s.advanced.paragraphs.map(p=><p key={p}>{p}</p>)}{s.advanced.code&&<pre><code>{s.advanced.code}</code></pre>}</details>}
    {s.next&&<p className="help-next"><b>下一步：</b>{s.next}</p>}{s.link&&<Button href={s.link.href}>{s.link.label} →</Button>}
  </section>;
}
