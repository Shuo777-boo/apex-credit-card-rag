// One content source for React, Markdown and standalone offline HTML. No dependencies.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const guide=JSON.parse(fs.readFileSync(path.join(root,'src/help/content.json'),'utf8'));
const out=path.join(root,'public/help');
fs.mkdirSync(out,{recursive:true});
const escape=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const p=value=>`<p>${escape(value)}</p>`;
const list=values=>`<ul>${values.map(v=>`<li>${escape(v)}</li>`).join('')}</ul>`;
const directoryTitle=s=>`<span class="help-toc-number" aria-hidden="true">${escape(s.title.split(' · ')[0])}</span><span class="help-toc-title">${escape(s.title.split(' · ').slice(1).join(' · '))}</span>`;
const inlineImages=new Map();
for(const s of guide.sections)for(const i of s.images??[]){
  const file=path.join(out,'images',i.file);
  if(!fs.existsSync(file))throw new Error(`Missing guide screenshot: ${file}. Run capture-help.py first.`);
  inlineImages.set(i.file,`data:image/webp;base64,${fs.readFileSync(file).toString('base64')}`);
}
function chapter(s){
  return `<section class="help-chapter" id="${s.id}" tabindex="-1"><div class="help-chapter-heading"><h2>${escape(s.title)}</h2>${p(s.summary)}</div>`+
    (s.goal?`<dl class="help-facts"><dt>操作目标</dt><dd>${escape(s.goal)}</dd><dt>适用与准备</dt><dd>${escape(s.audience)}</dd><dt>菜单路径</dt><dd>${escape(s.path)}</dd></dl>`:'')+
    (s.paragraphs??[]).map(p).join('')+
    (s.fields?`<h3>字段怎么填</h3><div class="help-table-scroll" tabindex="0" role="region" aria-label="字段说明，可横向滚动"><table><thead><tr><th>字段</th><th>是否必填 / 默认</th><th>示例值</th><th>填写建议与限制</th></tr></thead><tbody>${s.fields.map(f=>`<tr><th scope="row">${escape(f.name)}</th><td>${escape(f.required)}</td><td>${escape(f.example)}</td><td>${escape(f.advice)}</td></tr>`).join('')}</tbody></table></div>`:'')+
    (s.steps?`<h3>按顺序操作</h3><ol class="help-steps">${s.steps.map(t=>`<li>${p(t.action)}<div class="help-expected"><b>预期结果</b><span>${escape(t.expected)}</span></div></li>`).join('')}</ol>`:'')+
    (s.images??[]).map(i=>`<figure><button type="button" class="help-image-button" aria-label="放大图片：${escape(i.caption)}"><img src="${inlineImages.get(i.file)}" alt="${escape(i.alt)}" width="1440" height="1000" loading="lazy"><span>点击放大 · 支持 Enter</span></button><figcaption>${escape(i.caption)}</figcaption></figure>`).join('')+
    (s.checks?`<div class="help-checks"><h3>完成后检查</h3>${list(s.checks)}</div>`:'')+
    (s.errors?`<h3>遇到问题</h3>${s.errors.map(e=>`<div class="help-trouble"><h4>${escape(e.problem)}</h4>${p(e.solution)}</div>`).join('')}`:'')+
    (s.faqs??[]).map(f=>`<details class="help-faq"><summary>${escape(f.question)}</summary>${p(f.answer)}</details>`).join('')+
    (s.advanced?`<details class="help-advanced"><summary>${escape(s.advanced.title)}</summary>${s.advanced.paragraphs.map(p).join('')}${s.advanced.code?`<pre><code>${escape(s.advanced.code)}</code></pre>`:''}</details>`:'')+
    (s.next?`<p class="help-next"><b>下一步：</b>${escape(s.next)}</p>`:'')+
    (s.link?p(`在线功能入口：${s.link.label}；应用内路径 ${s.link.href}（离线说明书不连接业务服务）`):'')+'</section>';
}
const css=fs.readFileSync(path.join(root,'src/help/help.css'),'utf8');
const standalone=`body{margin:0;background:#f1f6ff;font:16px "Segoe UI","Microsoft YaHei",sans-serif;color:#203858}*{box-sizing:border-box}main{max-width:1400px;padding:24px;margin:auto}button,input{font:inherit}button{cursor:pointer}a{color:#0968fa}button:focus-visible,a:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #71a6ff;outline-offset:3px}.help-hero button,#clear-search{padding:8px 18px;background:#fff;border:1px solid #a9c9fa;border-radius:8px}.help-search input{width:100%;padding:12px;border:1px solid #c2d6f2;border-radius:8px}.help-search #search-status{grid-column:2}.offline-note{padding:18px;border:1px solid #a9c9fa;border-radius:10px;background:#eaf3ff;margin-bottom:20px}.help-content [hidden],.help-toc [hidden]{display:none!important}dialog{max-width:96vw;width:1500px;border:0;border-radius:12px;padding:16px}dialog::backdrop{background:#152543bc}dialog header{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:10px}dialog button{padding:8px 12px}dialog img{width:1440px;max-width:none;height:auto}.dialog-scroll{max-height:75vh;overflow:auto}#empty-results{background:white;border-radius:12px;padding:30px} .help-toc{top:16px}.help-hero .eyebrow{font-size:12px;color:#5178ad}@media(max-width:760px){main{padding:12px}.help-search #search-status{display:block}}@media print{.help-toc,.help-search,.help-image-button>span,.help-hero button{display:none}.help-layout{display:block}.help-chapter{break-inside:auto}.help-image-button img{max-height:500px}details>*{display:block!important}}`;
const script=`const input=document.getElementById('search');const chapters=[...document.querySelectorAll('.help-chapter')];const links=[...document.querySelectorAll('.help-toc a')];const status=document.getElementById('search-status');const empty=document.getElementById('empty-results');function search(){const terms=input.value.normalize('NFKC').trim().toLocaleLowerCase().split(/\\s+/);let count=0;for(const s of chapters){const hit=terms.every(t=>s.textContent.normalize('NFKC').toLocaleLowerCase().includes(t));s.hidden=!hit;links.find(a=>a.hash==='#'+s.id).hidden=!hit;if(hit)count++;if(input.value.trim())s.querySelectorAll('details').forEach(d=>d.open=hit);else s.querySelectorAll('details').forEach(d=>d.open=false)}status.textContent='找到 '+count+' 个相关章节';empty.hidden=count!==0}input.addEventListener('input',search);document.getElementById('clear-search').onclick=()=>{input.value='';search();input.focus()};const dialog=document.getElementById('preview');let trigger;document.querySelectorAll('.help-image-button').forEach(button=>button.onclick=()=>{trigger=button;const img=button.querySelector('img');const large=dialog.querySelector('img');large.src=img.src;large.alt=img.alt;document.getElementById('preview-title').textContent=button.closest('figure').querySelector('figcaption').textContent;dialog.showModal()});dialog.querySelector('button').onclick=()=>dialog.close();dialog.addEventListener('close',()=>trigger?.focus());dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close()});links.forEach(a=>a.onclick=()=>{requestAnimationFrame(()=>document.getElementById(a.hash.slice(1))?.focus({preventScroll:true}))});search();`;
const html=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>${escape(guide.title)} · Apex 离线说明书</title><style>${css}\n${standalone}</style></head><body><main class="help-page"><header class="help-hero"><div class="eyebrow">APEX / OFFLINE GUIDE</div><h1>${escape(guide.title)}</h1>${p(guide.intro)}${p('内容核对：'+guide.version+' · 本文件可断网阅读，全部图片已嵌入。')}<button onclick="window.print()">打印 / 保存 PDF</button></header><div class="help-search"><label for="search">搜索使用指南</label><input id="search" type="search" placeholder="搜索标题、正文、步骤和常见问题"><span id="search-status" role="status" aria-live="polite"></span><button id="clear-search">清除搜索</button></div><div class="help-layout"><aside class="help-toc"><nav aria-label="指南章节目录"><b>本页目录</b>${guide.sections.map(s=>`<a href="#${s.id}" aria-label="${escape(s.title)}">${directoryTitle(s)}</a>`).join('')}</nav></aside><div class="help-content"><div class="offline-note">${escape(guide.example)}</div><div id="empty-results" hidden>没有找到相关内容，请清除搜索或换个关键词。</div>${guide.sections.map(chapter).join('')}</div></div></main><dialog id="preview" aria-labelledby="preview-title"><header><strong id="preview-title">图片预览</strong><button autofocus>关闭图片</button></header><p>按 Escape 关闭，图片可滚动查看。</p><div class="dialog-scroll"><img alt=""></div></dialog><script>${script}</script></body></html>`;
let md=`# ${guide.title}\n\n${guide.intro}\n\n内容核对：${guide.version}\n\n${guide.example}\n\n## 目录\n\n${guide.sections.map(s=>`- ${s.title}`).join('\n')}\n`;
for(const s of guide.sections){
  md+=`\n## ${s.title}\n\n${s.summary}\n`;
  if(s.goal)md+=`\n**操作目标：** ${s.goal}\n\n**适用与准备：** ${s.audience}\n\n**菜单路径：** ${s.path}\n`;
  md+='\n'+(s.paragraphs??[]).join('\n\n')+'\n';
  if(s.fields)md+=`\n### 字段怎么填\n\n| 字段 | 是否必填 / 默认 | 示例值 | 填写建议与限制 |\n| --- | --- | --- | --- |\n${s.fields.map(f=>`| ${[f.name,f.required,f.example,f.advice].map(v=>v.replaceAll('|','\\|')).join(' | ')} |`).join('\n')}\n`;
  if(s.steps)md+=`\n### 按顺序操作\n\n${s.steps.map((t,i)=>`${i+1}. ${t.action}\n\n   **预期结果：** ${t.expected}`).join('\n\n')}\n`;
  for(const i of s.images??[])md+=`\n![${i.alt}](${inlineImages.get(i.file)})\n\n${i.caption}\n`;
  if(s.checks)md+=`\n### 完成后检查\n\n${s.checks.map(v=>'- '+v).join('\n')}\n`;
  for(const e of s.errors??[])md+=`\n**${e.problem}**\n\n${e.solution}\n`;
  for(const f of s.faqs??[])md+=`\n### ${f.question}\n\n${f.answer}\n`;
  if(s.advanced)md+=`\n### 进阶：${s.advanced.title}\n\n${s.advanced.paragraphs.join('\n\n')}\n${s.advanced.code?'\n```text\n'+s.advanced.code+'\n```\n':''}`;
  if(s.next)md+=`\n**下一步：** ${s.next}\n`;
  if(s.link)md+=`\n在线入口：${s.link.label}（应用路径 ${s.link.href}）。\n`;
}
fs.writeFileSync(path.join(out,'apex-guide.html'),html);
fs.writeFileSync(path.join(out,'apex-guide.md'),md);
console.log(`Help exported: ${guide.sections.length} chapters, ${inlineImages.size} embedded screenshots.`);
