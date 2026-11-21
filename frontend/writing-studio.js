/* Writing studio: manuscript list, chapter editor, LaTeX typesetting and quality reports on one page.
   Replaces views.writing and the start-writing dialog; reuses the existing actions/forms
   (section, confirm, revise, latex, start-writing) so the API contract is unchanged. */
const WS_TABS=[['chapters','章节编辑'],['latex','LaTeX 排版'],['quality','质量报告']];
const WS_STAGES=['核对材料','分章写作','审稿与修改','中文译文','完成'];

/* ---------- pure helpers (tests/writing-studio.cjs) ---------- */
function wsPlaceholders(md){
  const text=String(md||'');
  // Bilingual chapters carry each placeholder twice (English and Chinese); keep one wording.
  const uniq=list=>{const all=[...new Set(list.map(x=>x.trim()).filter(Boolean))];const zh=all.filter(x=>/[一-鿿]/.test(x)),en=all.filter(x=>!/[一-鿿]/.test(x));return zh.length>=en.length?zh:en;};
  return {
    skill:uniq([...text.matchAll(/\[SKILL_PENDING:?\s*([^\]]*)\]/g)].map(m=>m[1]||'待补 Skill')),
    author:uniq([...text.matchAll(/\[AUTHOR_INPUT_NEEDED:?\s*([^\]]*)\]/g)].map(m=>m[1]||'作者填写')),
  };
}

function wsStage(doc){
  const p=String(doc?.progress||'');
  if(doc?.status==='ready')return {stage:4,ratio:1};
  if(/审稿/.test(p))return {stage:2,ratio:.84};
  if(/译文|翻译/.test(p))return {stage:3,ratio:.93};
  const m=p.match(/[（(](\d+)\/(\d+)[）)]/);
  if(m){const i=Number(m[1]),n=Math.max(Number(m[2]),1);return {stage:1,ratio:.08+.72*Math.min(i-1,n)/n};}
  return {stage:0,ratio:.04};
}

function wsQuality(reports){
  const out={storms:null,review:null};
  for(const r of reports||[]){
    const md=String(r.markdown||'');
    if(/storms/i.test(r.name||'')){
      const m=md.match(/Reported (\d+) · author input needed (\d+) · missing (\d+) \(of (\d+)\)/);
      if(m)out.storms={reported:+m[1],author:+m[2],missing:+m[3],total:+m[4]};
    }
    if(/review/i.test(r.name||'')){
      const levels=[...md.matchAll(/^## \d+\. \[(\w+)\]/gm)].map(x=>x[1]);
      const done=[...md.matchAll(/^Action:\s*(.+)$/gm)].map(x=>x[1].trim());
      out.review={total:levels.length,major:levels.filter(x=>x==='major').length,revised:done.filter(a=>/^revised|^deleted/.test(a)).length};
    }
  }
  return out;
}

/* A section stores both languages: "<!-- lang:en -->…<!-- lang:zh -->…" (saved by the editor)
   or English, a standalone "中文" line, then Chinese (written by the pipeline). */
function wsSplitLang(md){
  const t=String(md||'');
  if(t.includes('<!-- lang:en -->')&&t.includes('<!-- lang:zh -->')){
    const after=t.split('<!-- lang:en -->')[1];const cut=after.indexOf('<!-- lang:zh -->');
    const rest=after.slice(cut+'<!-- lang:zh -->'.length);const sh=rest.indexOf('<!-- lang:shared -->');
    return {mode:'marked',en:after.slice(0,cut).trim(),zh:(sh<0?rest:rest.slice(0,sh)).trim(),shared:sh<0?'':rest.slice(sh+'<!-- lang:shared -->'.length).trim()};
  }
  const m=t.match(/^[ \t]*(?:#{1,6}[ \t]*)?中文[ \t]*$/m);
  if(m)return {mode:'label',en:t.slice(0,m.index).trim(),zh:t.slice(m.index+m[0].length).trim(),label:m[0].trim()};
  return {mode:'single',text:t};
}
function wsLangText(parts,lang){return parts.mode==='single'?parts.text:(lang==='en'?parts.en:parts.zh);}
function wsJoinLang(parts,lang,value){
  if(parts.mode==='single')return value;
  const en=lang==='en'?value.trim():parts.en,zh=lang==='en'?parts.zh:value.trim();
  const NL='\n';
  if(parts.mode==='marked')return ['<!-- lang:en -->',en,'','<!-- lang:zh -->',zh].concat(parts.shared?['','<!-- lang:shared -->',parts.shared]:[]).join(NL)+NL;
  return [en,'',parts.label,'',zh].join(NL)+NL;
}

function wsFigureCount(markdowns){
  const seen=new Set();
  for(const md of markdowns)for(const m of String(md||'').matchAll(/!\[[^\]]*\]\((figures\/[^)\s]+)\)/g))seen.add(m[1]);
  return seen.size;
}

function wsCiteKeys(md){return [...new Set([...String(md||'').matchAll(/\[@([^\]]+)\]/g)].flatMap(m=>m[1].split(/[;,]\s*@?/)).map(k=>k.replace(/^@/,'').trim()).filter(Boolean))];}

function wsInline(text){
  return esc(text)
    .replace(/`([^`]+)`/g,'<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>')
    .replace(/\[@([^\]]+)\]/g,(_m,k)=>`<span class="ws-cite">${k.replace(/@/g,'')}</span>`);
}

function wsCell(text){
  const m=String(text).match(/^(.*?)\s*\((reported|author_input|missing|partial)\)\s*$/);
  return m?`<span class="ws-pill ${m[2]}">${esc(m[1]||m[2])}</span>`:wsInline(text);
}

function wsMarkdown(md){
  const lines=String(md||'').replace(/\r/g,'').split('\n');
  const out=[];let i=0;
  const block=/^(#{1,4}\s|>|\s*\||\s*([-*]|\d+\.)\s)/;
  while(i<lines.length){
    const line=lines[i];let m;
    if(!line.trim()){i++;continue;}
    if((m=line.match(/^(#{1,4})\s+(.*)$/))){const lv=Math.min(m[1].length+2,6);out.push(`<h${lv}>${wsInline(m[2])}</h${lv}>`);i++;continue;}
    if(/^\s*\|/.test(line)){
      const rows=[];while(i<lines.length&&/^\s*\|/.test(lines[i]))rows.push(lines[i++]);
      const cells=r=>r.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(c=>c.trim());
      const body=rows.filter(r=>!/^\s*\|[\s:|-]+\|?\s*$/.test(r));
      const [first,...rest]=body;
      if(first)out.push(`<div class="ws-table"><table><thead><tr>${cells(first).map(c=>`<th>${wsInline(c)}</th>`).join('')}</tr></thead><tbody>${rest.map(r=>`<tr>${cells(r).map(c=>`<td>${wsCell(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
      continue;
    }
    if(/^>/.test(line)){const q=[];while(i<lines.length&&/^>/.test(lines[i]))q.push(lines[i++].replace(/^>\s?/,''));out.push(`<blockquote>${wsInline(q.join(' '))}</blockquote>`);continue;}
    if(/^\s*([-*]|\d+\.)\s+/.test(line)){
      const tag=/^\s*\d+\./.test(line)?'ol':'ul';const li=[];
      while(i<lines.length&&/^\s*([-*]|\d+\.)\s+/.test(lines[i]))li.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/,''));
      out.push(`<${tag}>${li.map(x=>`<li>${wsInline(x)}</li>`).join('')}</${tag}>`);continue;
    }
    // "Problem: …" / "Action: …" lines in the reports are separate fields, not one paragraph.
    const label=/^([A-Z][A-Za-z ]{1,24}):\s/;
    const para=[line];i++;
    while(i<lines.length&&lines[i].trim()&&!block.test(lines[i])&&!label.test(lines[i]))para.push(lines[i++]);
    const lm=para[0].match(label);
    out.push(lm?`<p><b class="ws-field">${esc(lm[1])}</b> ${wsInline(para.join(' ').slice(lm[0].length))}</p>`:`<p>${wsInline(para.join(' '))}</p>`);
  }
  return out.join('');
}

function wsDate(value){
  const d=new Date(value);if(Number.isNaN(d.getTime()))return '';
  const pad=n=>String(n).padStart(2,'0');
  return `${d.getMonth()+1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function wsStatus(s){
  if(s.status==='running')return {cls:'run',label:'生成中'};
  if(s.status==='failed')return {cls:'missing',label:'没有完成'};
  const total=s.total_sections||Object.keys(s.sections||{}).length;
  const done=s.confirmed_count||0;
  if(total&&done>=total)return {cls:'ok',label:'已全部确认'};
  return {cls:'draft',label:total?`草稿 · 已确认 ${done}/${total}`:'草稿'};
}

/* ---------- page ---------- */
function wsTab(){
  if(!state.writingTab){try{state.writingTab=localStorage.getItem('bioflow.writingTab')||'';}catch{}}
  return WS_TABS.some(([k])=>k===state.writingTab)?state.writingTab:'chapters';
}

function wsHead(templateLabel,sessions=[]){
  return `<div class="section-head ws-head"><div><div class="eyebrow">BIOFLOW / WRITING</div><h1>研究稿件</h1><p class="muted">数字来自分析结果，引用来自个人文献库，图来自图表模块；每一段都经过 Skills 核对。</p></div>`+
    `<div class="ws-head-actions">${sessions.length?wsList(sessions):''}${button('＋ 新建稿件','new-writing','class="primary"')}`+
    `<details class="ws-menu"><summary>更多</summary><div class="ws-menu-pop">${button('关联已有稿件','import-sessions')}${button(esc(templateLabel),'template-library')}${button('模型配置','llm')}${button('刷新','refresh')}</div></details></div></div>`;
}

function wsList(sessions){
  return `<details class="ws-menu ws-switch"><summary>全部稿件 <span class="ws-count">${sessions.length}</span></summary><div class="ws-menu-pop ws-list" aria-label="稿件列表"><input class="ws-search" name="wsSearch" type="search" placeholder="搜索稿件" aria-label="搜索稿件"><div class="ws-items">`+
    sessions.map(s=>{const st=wsStatus(s);const on=s.session_id===state.session;
      return `<button type="button" class="ws-item${on?' on':''}" data-action="session" data-id="${esc(s.session_id)}" data-search="${esc((s.title||'')+' '+s.session_id)}"${on?' aria-current="true"':''}>`+
        `<span class="ws-item-title">${esc(s.title||s.session_id)}</span>`+
        `<span class="ws-item-meta"><i class="ws-dot ${st.cls}"></i>${esc(st.label)}${s.created_at?' · '+esc(wsDate(s.created_at)):''}</span></button>`;}).join('')+
    `</div></div></details>`;
}

function wsRunning(doc){
  const failed=doc.status==='failed';const st=wsStage(doc);
  return `<div class="ws-card ws-progress"><div class="ws-doc-title"><h2>${esc(doc.title||'正在生成稿件')}</h2><span class="ws-pill ${failed?'missing':'run'}">${failed?'没有完成':'生成中'}</span></div>`+
    `<ol class="ws-stages">${WS_STAGES.map((name,i)=>`<li class="${i<st.stage?'done':(i===st.stage&&!failed?'now':'')}"><span>${i<st.stage?'✓':i+1}</span>${esc(name)}</li>`).join('')}</ol>`+
    `<div class="ws-bar${failed?' failed':''}"><i style="width:${Math.round(st.ratio*100)}%"></i></div>`+
    `<p class="ws-progress-text">${esc(failed?(doc.error||doc.progress||'生成没有完成'):(doc.progress||'已开始'))}</p>`+
    `<p class="caption">${failed?'可以调整材料后重新生成。':'生成在后台进行，可以离开这个页面，稍后回来查看。'}</p></div>`;
}

function wsMetric(label,value,hint,opts={}){
  const bar=typeof opts.ratio==='number'?`<span class="ws-meter"><i style="width:${Math.round(Math.max(0,Math.min(1,opts.ratio))*100)}%"></i></span>`:'';
  const tag=opts.action?'button':'div';
  const attrs=opts.action?`type="button" data-action="${opts.action}" ${opts.attrs||''}`:'';
  return `<${tag} class="ws-metric${opts.tone?' '+opts.tone:''}" ${attrs}><span class="ws-metric-label">${esc(label)}</span><span class="ws-metric-value">${esc(value)}</span>${bar}<span class="ws-metric-hint">${esc(hint)}</span></${tag}>`;
}

async function wsReady(doc){
  const reports=(await service('writing',`/api/sessions/${id(doc.session_id)}/reports`).catch(()=>({items:[]}))).items||[];
  const q=wsQuality(reports);
  const order=(doc.section_order||[]).filter(k=>doc.sections[k]);
  const holes=order.map(k=>wsPlaceholders(doc.sections[k].markdown));
  const nAuthor=holes.reduce((n,h)=>n+h.author.length,0),nSkill=holes.reduce((n,h)=>n+h.skill.length,0);
  const firstHole=order[holes.findIndex(h=>h.author.length||h.skill.length)]||'';
  const confirmed=order.filter(k=>doc.sections[k].confirmed).length;
  const refs=(doc.source?.references||[]).length;
  const figures=wsFigureCount(order.map(k=>doc.sections[k].markdown));
  const tab=wsTab();
  const lang=state.paperLanguage==='en'?'en':'zh';
  const header=`<div class="ws-doc-title"><div><h2>${esc(doc.title)}</h2><div class="ws-chips"><span>${order.length} 个章节</span><span>${refs} 篇参考文献</span><span>${figures} 张图</span>${doc.created_at?`<span>${esc(wsDate(doc.created_at))}</span>`:''}</div></div>`+
    `<div class="ws-doc-actions"><div class="ws-seg" role="group" aria-label="排版语言">${button('中文','paper-lang',`type="button" data-lang="zh"${lang==='zh'?' class="on" aria-pressed="true"':''}`)}${button('English','paper-lang',`type="button" data-lang="en"${lang==='en'?' class="on" aria-pressed="true"':''}`)}</div>${button('导出','ws-export','type="button" class="primary"')}</div></div>`;
  const metrics=`<div class="ws-metrics">`+
    wsMetric('章节确认',`${confirmed}/${order.length}`,confirmed===order.length?'全部确认，可导出 Word / PDF':'逐章检查后点「确认本章」',{ratio:order.length?confirmed/order.length:0,action:'ws-tab',attrs:'data-tab="chapters"',tone:confirmed===order.length?'good':''})+
    (q.storms?wsMetric('STORMS 报告规范',`${q.storms.reported}/${q.storms.total}`,`${q.storms.author} 项需作者补充 · ${q.storms.missing} 项缺失`,{ratio:q.storms.reported/q.storms.total,action:'ws-tab',attrs:'data-tab="quality"'}):wsMetric('STORMS 报告规范','—','本稿没有规范检查报告'))+
    (q.review?wsMetric('模拟审稿',`${q.review.total} 条意见`,`主要 ${q.review.major} 条 · 已定向修改 ${q.review.revised} 条`,{action:'ws-tab',attrs:'data-tab="quality"'}):wsMetric('模拟审稿','—','本稿没有审稿报告'))+
    wsMetric('待补内容',`${nAuthor+nSkill} 处`,`作者填写 ${nAuthor} · 待补 Skill ${nSkill}`,firstHole?{action:'ws-goto',attrs:`data-id="${esc(firstHole)}"`,tone:'warn'}:{tone:'good'})+
    `</div>`;
  const tabs=`<div class="ws-tabs" role="tablist">${WS_TABS.map(([k,l])=>`<button type="button" role="tab" data-action="ws-tab" data-tab="${k}" class="${tab===k?'on':''}" aria-selected="${tab===k}">${l}${k==='quality'&&reports.length?`<span class="ws-count">${reports.length}</span>`:''}</button>`).join('')}</div>`;
  let body='';
  if(tab==='chapters')body=wsChapters(doc,order,holes);
  else if(tab==='latex')body=await wsLatex(doc,lang);
  else body=wsReports(reports);
  return `<div class="ws-doc">${header}${metrics}${tabs}<div class="ws-panel">${body}</div></div>`;
}

function wsChapters(doc,order,holes){
  if(!order.includes(state.chapter))state.chapter=order[0]||'';
  if(!state.chapter)return '<p class="caption">这篇稿件没有章节。</p>';
  const sec=doc.sections[state.chapter];const hole=holes[order.indexOf(state.chapter)];
  const lang=state.paperLanguage==='en'?'en':'zh';const parts=wsSplitLang(sec.markdown);state.wsSplit={parts,lang};
  const shown=wsLangText(parts,lang);const langName=lang==='en'?'English':'中文';
  const outline=`<nav class="ws-outline" aria-label="章节">${order.map((k,i)=>{const s=doc.sections[k];const h=holes[i];
      const flag=s.confirmed?'ok':(h.author.length||h.skill.length)?'warn':'draft';
      return `<button type="button" data-action="chapter" data-id="${esc(k)}" class="ws-sec${k===state.chapter?' on':''}"><span class="ws-sec-n">${String(i+1).padStart(2,'0')}</span><span class="ws-sec-t">${esc(s.title_zh||s.title||k)}<small>${esc(s.title||'')}</small></span><i class="ws-dot ${flag}" title="${({ok:'已确认',warn:'有待补内容',draft:'草稿'})[flag]}"></i></button>`;}).join('')}</nav>`;
  const callouts=[...hole.author.map(t=>`<li><b>作者填写</b>${esc(t)}</li>`),...hole.skill.map(t=>`<li><b>待补 Skill</b>${esc(t)}</li>`)].join('');
  const refs=new Map((doc.source?.references||[]).map(r=>[r.cite_id,r]));
  const cited=wsCiteKeys(sec.markdown).map(k=>refs.get(k)||{cite_id:k});
  const chat=(sec.chat||[]).map(m=>`<li class="${m.role==='user'?'me':'ai'}">${esc(m.content)}</li>`).join('');
  const title=esc(sec.title_zh||sec.title||state.chapter);
  const drawer=`<button type="button" class="ws-fab" data-action="ws-chat" aria-label="让模型修改本章" title="让模型修改本章"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4A2.5 2.5 0 0 1 4 13.5Z"/><path d="M9 9.5h6M9 12.5h3.5"/></svg><span>AI 修改</span>${(sec.chat||[]).length?`<i>${(sec.chat||[]).filter(m=>m.role==='user').length}</i>`:''}</button>`+
    `<aside class="ws-drawer${state.wsChatOpen?' open':''}" aria-label="与模型对话修改本章"><header><div><h4>与模型对话修改</h4><p>${title} · 修改后仍会核对数字和引用</p></div><button type="button" class="ws-x" data-action="ws-chat" aria-label="关闭">×</button></header>`+
    `<ul class="ws-chat">${chat||'<li class="ws-chat-empty">说明要改什么，例如「第二段更简洁」「补充样本数的说明」「语气更客观」。模型会改写本章并保存，你可以继续追问。</li>'}</ul>`+
    `<form data-form="revise" class="ws-chat-form"><textarea name="message" rows="3" required placeholder="输入修改要求，Ctrl+Enter 发送"></textarea><button class="primary">发送</button></form></aside>`;
  return `<div class="ws-chapters">${outline}`+
    `<form data-form="section" class="ws-editor"><div class="ws-editor-head"><div><h3>${esc(sec.title_zh||sec.title||state.chapter)} <small>${esc(sec.title||'')}</small></h3>`+
    `<p class="ws-save-state">${parts.mode==='single'?'':`<span class="ws-pill">${langName}</span> `}${shown.length} 字符${sec.updated_at?' · 更新于 '+esc(wsDate(sec.updated_at)):''}</p></div>`+
    `<div class="ws-editor-actions"><button type="submit">保存</button>${sec.confirmed?'<span class="ws-pill ok">已确认</span>':button('确认本章','confirm','type="button" class="primary"')}</div></div>`+
    (callouts?`<ul class="ws-callout">${callouts}</ul>`:'')+
    `<input type="hidden" name="markdown" value="${esc(sec.markdown||'')}">`+
    `<textarea name="markdown_view" class="ws-textarea" spellcheck="false" aria-label="章节正文（${langName}）" placeholder="${lang==='en'?'本章还没有英文版。':'本章还没有中文版。'}">${esc(shown)}</textarea>`+
    `<div class="ws-cited-box"><h4>本章引用 <span class="ws-count">${cited.length}</span></h4>${cited.length?`<ol class="ws-cited">${cited.map(r=>`<li><b>${esc(r.cite_id)}</b>${r.title?`<span>${esc(r.title)}${r.year?' · '+esc(r.year):''}</span>`:'<span>不在参考文献列表中</span>'}</li>`).join('')}</ol>`:'<p class="caption">本章没有引用文献。</p>'}</div>`+
    `</form>${drawer}</div>`;
}

async function wsLatex(doc,lang){
  const preview=await service('writing',`/api/sessions/${id(doc.session_id)}/latex?language=${lang}&project_id=${id(state.project)}`);
  const pdf=preview.pdf_ready?`/services/writing/api/sessions/${id(doc.session_id)}/latex/pdf?v=${Date.now()}`:'';
  setTimeout(()=>{if(state.page==='writing'&&state.session===doc.session_id&&state.paperLanguage===lang&&wsTab()==='latex'&&!preview.pdf_ready){const form=document.querySelector('form[data-form="latex"]');if(form)forms.latex(form).catch(e=>notify(e.message));}},0);
  return `<div class="latex-studio ws-latex"><form data-form="latex"><div class="ws-latex-bar"><span>manuscript.tex · ${lang==='en'?'English':'中文'}</span><button class="primary">编译 PDF</button></div><textarea name="tex" class="latex-source" aria-label="LaTeX 源码" spellcheck="false">${esc(preview.tex||'')}</textarea><p class="latex-note"></p></form>`+
    `<div class="latex-preview">${pdf?`<iframe title="论文 PDF" src="${pdf}"></iframe>`:'<iframe title="论文 PDF" hidden></iframe><p class="latex-wait">正在编译 PDF…</p>'}</div></div>`;
}

function wsReports(reports){
  if(!reports.length)return '<div class="ws-empty"><h3>还没有质量报告</h3><p>用分析结果包并连接模型生成的稿件，会自动做模拟审稿和 STORMS 报告规范检查。</p></div>';
  const order=[...reports].sort((a,b)=>/storms/i.test(a.name)?-1:/storms/i.test(b.name)?1:0);
  return `<div class="ws-reports">${order.map(r=>`<article class="ws-card ws-report"><div class="ws-md">${wsMarkdown(r.markdown)}</div></article>`).join('')}</div>`;
}

views.writing=async()=>{
  const sessions=await linked('session','writing','/api/sessions');
  let templateLabel='LaTeX 模板库';
  try{const library=await api(pa('/latex-template'));const chosen=(library.items||[]).find(item=>item.id===library.active);if(chosen)templateLabel=`LaTeX 模板 · ${chosen.name}`;}catch{}
  if(!sessions.some(s=>s.session_id===state.session))state.session=sessions[0]?.session_id||'';
  const top=wsHead(templateLabel,sessions);
  if(!state.session)return top+`<div class="ws-empty ws-card"><h3>开始第一篇稿件</h3><p>选择分析结果、个人文献库里的参考文献和图表模块里的图，按文章类型生成章节。</p>${button('＋ 新建稿件','new-writing','class="primary"')}</div>`;
  const doc=await service('writing',`/api/sessions/${id(state.session)}`);state.doc=doc;
  let main;
  if(doc.status==='ready')main=await wsReady(doc);
  else{
    main=wsRunning(doc);
    if(doc.status==='running')pollTimer=setTimeout(()=>{if(state.page==='writing'&&state.session===doc.session_id)render().catch(e=>notify(e.message));},3000);
  }
  return top+`<div class="ws"><div class="ws-main">${main}</div></div>`;
};

/* ---------- actions ---------- */
async function wsSaveChapter(){if(typeof saveActiveChapter==='function')await saveActiveChapter();}
const wsPaperLang=actions['paper-lang'];
actions['paper-lang']=async el=>{await wsSaveChapter();return wsPaperLang(el);};
// Chat drawer: toggled in place (no re-render) so unsaved editor text stays put.
actions['ws-chat']=()=>{state.wsChatOpen=!state.wsChatOpen;const d=root.querySelector('.ws-drawer');if(!d)return;d.classList.toggle('open',state.wsChatOpen);
  if(state.wsChatOpen){const list=d.querySelector('.ws-chat');list.scrollTop=list.scrollHeight;d.querySelector('textarea')?.focus();}};
// Chat revision: saves the editor first, then asks the chapter-chat-reviser skill to change
// only the language half on screen; the reply (applied, unchanged or rejected) lands in the drawer.
forms.revise=async f=>{
  const list=f.closest('.ws-drawer')?.querySelector('.ws-chat');const text=f.elements.namedItem('message').value.trim();
  if(!text)return;
  if(list){list.querySelector('.ws-chat-empty')?.remove();list.insertAdjacentHTML('beforeend',`<li class="me">${esc(text)}</li><li class="ai ws-typing">正在修改本章，并核对数字、引用和图…</li>`);list.scrollTop=list.scrollHeight;}
  state.wsChatOpen=true;
  const base=`/api/sessions/${id(state.session)}/sections/${id(state.chapter)}`;
  try{
    const full=root.querySelector('form[data-form="section"] [name="markdown"]');
    if(full&&full.value!==state.doc?.sections?.[state.chapter]?.markdown)await service('writing',base,'PUT',{markdown:full.value});
    const result=await service('writing',base+'/chat','POST',{message:text,use_llm:true,language:state.paperLanguage==='en'?'en':'zh'});
    notify(result.applied?'本章已按要求修改，请在编辑器中审阅':'这次没有修改原文，原因见对话框');
  }catch(err){list?.querySelector('.ws-typing')?.remove();throw err;}
  await render();
};
actions['ws-tab']=async el=>{await wsSaveChapter();state.writingTab=el.dataset.tab;try{localStorage.setItem('bioflow.writingTab',state.writingTab);}catch{}return render();};
actions['ws-goto']=async el=>{await wsSaveChapter();state.writingTab='chapters';state.chapter=el.dataset.id;return render();};

actions['ws-export']=async()=>{
  const doc=state.doc;if(!doc)return;
  const order=(doc.section_order||[]).filter(k=>doc.sections[k]);
  const pending=order.filter(k=>!doc.sections[k].confirmed);
  const lang=state.paperLanguage==='en'?'en':'zh';
  const pdf=`/services/writing/api/sessions/${id(doc.session_id)}/latex/pdf`;
  modal('导出稿件',`<div class="ws-export">`+
    `<section class="ws-card"><h3>LaTeX 排版 PDF</h3><p class="caption">「LaTeX 排版」页最近一次编译的 ${lang==='en'?'英文':'中文'}版 PDF，不需要先确认章节。</p><a class="ws-btn" href="${pdf}" target="_blank" rel="noopener">打开 PDF</a></section>`+
    `<section class="ws-card"><h3>Word · Markdown · PDF</h3><p class="caption">统一排版，引用按出现顺序编号。${pending.length?`还有 ${pending.length} 个章节没有确认：${esc(pending.map(k=>doc.sections[k].title_zh||k).join('、'))}。`:'全部章节已确认。'}</p>`+
    `<div class="ws-seg" role="group" aria-label="导出语言">${['zh','en'].map(l=>`<button type="button" data-action="ws-export-lang" data-lang="${l}" class="${l===lang?'on':''}">${l==='en'?'English':'中文'}</button>`).join('')}</div>`+
    `<div class="row">${button(pending.length?'确认全部章节并导出':'导出','ws-export-run',`type="button" class="primary" data-lang="${lang}"${pending.length?' data-confirm-all="1"':''}`)}</div><div class="ws-export-links"></div></section></div>`);
  root.querySelector('.overlay .modal')?.classList.add('ws-modal-md');
};
actions['ws-export-lang']=el=>{const box=el.closest('.ws-card');box.querySelectorAll('[data-action="ws-export-lang"]').forEach(b=>b.classList.toggle('on',b===el));box.querySelector('[data-action="ws-export-run"]').dataset.lang=el.dataset.lang;};
actions['ws-export-run']=async el=>{
  const doc=state.doc;const links=el.closest('.ws-card').querySelector('.ws-export-links');
  if(el.dataset.confirmAll){
    for(const k of (doc.section_order||[]).filter(k=>doc.sections[k]&&!doc.sections[k].confirmed)){links.textContent=`正在确认 ${doc.sections[k].title_zh||k}…`;await service('writing',`/api/sessions/${id(doc.session_id)}/sections/${id(k)}/confirm`,'POST',{confirmed:true});}
    delete el.dataset.confirmAll;
  }
  links.textContent='正在排版导出…';
  await service('writing',`/api/sessions/${id(doc.session_id)}/export`,'POST',{language:el.dataset.lang==='en'?'en':'zh'});
  links.innerHTML=['docx','pdf','md'].map(f=>`<a class="ws-btn" href="/services/writing/api/sessions/${id(doc.session_id)}/download/${f}" target="_blank" rel="noopener">下载 ${({docx:'Word',pdf:'PDF',md:'Markdown'})[f]}</a>`).join('');
  el.textContent='重新导出';
  render().catch(()=>{});
};

const wsSectionForm=forms.section;
forms.section=async f=>{await wsSectionForm(f);const sec=state.doc?.sections?.[state.chapter];if(sec)sec.markdown=f.elements.namedItem('markdown').value;const note=root.querySelector('.ws-save-state');if(note){note.textContent='已保存';note.classList.remove('dirty');}};

/* ---------- start-writing dialog ---------- */
function wsStep(n,title,hint,body){return `<section class="ws-step"><header><span class="ws-num">${n}</span><div><h3>${esc(title)}</h3>${hint?`<p>${esc(hint)}</p>`:''}</div></header><div class="ws-step-body">${body}</div></section>`;}

function wsRefsPicker(library){
  const pid=state.project;
  const rows=library.map((p,i)=>{const m=p.metadata||{};const doi=String(m.doi||'').trim();const linkedRef=(p.projects||[]).some(x=>x.id===pid);
    const authors=String(m.authors||'').split(/[;,]/)[0].trim();const who=authors?authors+(String(m.authors).split(/[;,]/).length>1?' 等':''):'';
    const meta=[who,m.year,m.journal||m.venue,doi?'DOI '+doi:'无 DOI，暂不能引用'].filter(Boolean).map(String).join(' · ');
    return `<label class="ws-ref${doi?'':' off'}" data-search="${esc([m.title,p.name,m.authors,doi].join(' ').toLowerCase())}"><input type="checkbox" name="writing-ref" value="${i}"${doi?'':' disabled'}${doi&&linkedRef?' checked':''}><span><b>${esc(m.title||p.name||'')}</b><small>${esc(meta)}</small></span></label>`;}).join('');
  return `<div class="ws-refs"><div class="ws-refs-bar"><input type="search" class="ws-ref-search" placeholder="按标题、作者或 DOI 筛选" aria-label="筛选文献"><span class="ws-ref-count"></span></div><div class="ws-ref-list">${rows||'<p class="caption">个人文献库暂无文献。可以在「资料」页加入文献。</p>'}</div></div>`;
}

function wsFigurePicker(figures){
  const usable=writingUsableFigures(figures);const chosen=new Set(state.writingFigureDraft||[]);const pid=state.project;
  if(!usable.length)return '<p class="caption">当前项目还没有保存的真实数据图。可在「图表」页生成并保存后再来选择。</p>';
  return `<div class="ws-figs">${usable.map(f=>{const title=(f.parameters||{}).title||f.chart;const task=String((f.source||{}).task_id||'').slice(0,8);
    return `<label class="ws-fig"><input type="checkbox" name="writing-fig" value="${esc(f.id)}"${chosen.has(f.id)?' checked':''}><span class="ws-fig-n"></span>`+
      `<img alt="" loading="lazy" src="/api/projects/${encodeURIComponent(pid)}/figures/${encodeURIComponent(f.id)}/file/png">`+
      `<span class="ws-fig-t">${esc(title)}<small>${esc(f.chart)}${task?' · 任务 '+esc(task):''}</small></span></label>`;}).join('')}</div>`;
}

function wsSyncPicks(form){
  const refs=[...form.querySelectorAll('input[name="writing-ref"]')];const count=form.querySelector('.ws-ref-count');
  if(count)count.textContent=`已选 ${refs.filter(x=>x.checked).length} / ${refs.filter(x=>!x.disabled).length} 篇`;
  let n=0;form.querySelectorAll('.ws-fig').forEach(card=>{const on=card.querySelector('input').checked;card.classList.toggle('on',on);card.querySelector('.ws-fig-n').textContent=on?`Figure ${++n}`:'';});
}

actions['new-writing']=async()=>{
  const [skillCatalog,picker,pkg,library,sectionLib,figs]=await Promise.all([
    service('writing','/api/writing-skills').catch(()=>({items:[]})),taskPicker(),
    service('writing','/api/upstream/analysis/packages').catch(()=>({packages:[]})),
    api('/api/library/literature').catch(()=>({items:[]})),
    service('writing','/api/section-library').catch(()=>null),
    api(pa('/figures')).catch(()=>({items:[]}))]);
  const packages=items(pkg.packages||[]);const lib=items(library);state.writingLibrary=lib;
  const skills=chosenPaperSkills(skillCatalog);const skillLabels=skills.map(name=>(skillCatalog.items||[]).find(x=>x.name===name)?.display_name||name);const papers=writingPaperSelection();const kb=current()?.knowledge_base_id;
  const source=`<div class="ws-grid2">${field('分析来源',select('source',[['task','当前项目分析任务'],['package','本地分析包']]))}${field('分析任务',picker)}${field('分析包',select('package',packages.map(p=>{const path=p.path||p.run_dir||p.analysis_run_dir||'';return [path,p.label||p.name||path];}))+'<p class="caption ws-path" id="writing-package-path"></p>')}</div>`;
  const advanced=`<details class="ws-adv"${papers.length||skills.length?' open':''}><summary>高级选项：知识库检索、写作 Skills</summary><div class="ws-adv-body">`+
    `<label class="ws-check-row"><input name="live" type="checkbox"${kb?' checked':''}> 同时按研究目标检索当前项目知识库</label>`+
    `<div class="ws-adv-row"><div><b>知识库文献</b><p class="caption">${esc(papers.map(p=>p.name).join('、')||'未指定，按研究目标检索。')}</p><p class="caption" id="writing-paper-note"></p></div>${button('去文献库选择','pick-papers','type="button"')}</div>`+
    `<div class="ws-adv-row"><div><b>写作 Skills</b><p class="caption">${esc(skillLabels.join('、')||'未额外选择，使用默认的 Skills 写作流程。')}</p></div>${button('去 Skills 库选择','pick-paper-skills','type="button"')}</div>`+
    (skills.includes('nature-response')?field('审稿意见／编辑来信（已选 nature-response，必填）','<textarea name="reviewer_comments"></textarea>'):'<input type="hidden" name="reviewer_comments" value="">')+
    `</div></details>`;
  modal('新建研究稿件',`<form data-form="start-writing" class="ws-wizard">`+
    wsStep(1,'研究目标','一两句话写清研究对象、数据和要回答的问题，会用来拟定标题和引言。','<textarea name="brief" required rows="3" placeholder="例：PRJEB33098 九例人粪便样本的鸟枪法宏基因组数据：质控、去宿主、物种与功能谱"></textarea>')+
    wsStep(2,'分析结果','稿件里的数字只取自这里选的分析结果。',source)+
    (sectionLib?wsStep(3,'文章类型与章节','',writingSectionsBox(sectionLib)):'')+
    wsStep(sectionLib?4:3,'参考文献','来自个人文献库。按 DOI 获取摘要，只引用摘要里写明的内容。已关联当前项目的文献默认勾选。',wsRefsPicker(lib))+
    wsStep(sectionLib?5:4,'论文图','来自图表模块，只列出已保存的真实数据图。请选与分析结果一致的图。',wsFigurePicker(items(figs)))+
    advanced+
    `<div id="writing-progress" class="ws-check" aria-live="polite"></div>`+
    `<div class="ws-modal-foot"><span class="caption">先核对材料和缺项，确认后再生成。</span><button class="primary">检查写作材料</button></div></form>`);
  const modalEl=root.querySelector('.overlay .modal');modalEl?.classList.add('ws-modal');
  const form=root.querySelector('form[data-form="start-writing"]');
  if(packages.length)form.elements.namedItem('source').value='package';
  applyWritingDraft(form);syncWritingSource(form);
  if(sectionLib)bindWritingSections(form,sectionLib);
  wsSyncPicks(form);
  form.addEventListener('change',e=>{if(e.target.name==='writing-ref'||e.target.name==='writing-fig'){delete form.dataset.materialFingerprint;wsSyncPicks(form);}});
  form.querySelector('.ws-ref-search')?.addEventListener('input',e=>{const term=e.target.value.trim().toLowerCase();form.querySelectorAll('.ws-ref').forEach(r=>{r.hidden=!!term&&!r.dataset.search.includes(term);});});
};

/* ---------- small conveniences ---------- */
root.addEventListener('input',e=>{
  if(e.target.name==='wsSearch'){const term=e.target.value.trim().toLowerCase();root.querySelectorAll('.ws-item').forEach(el=>{el.hidden=!!term&&!el.dataset.search.toLowerCase().includes(term);});}
  if(e.target.matches('.ws-textarea')){const full=e.target.form?.elements.namedItem('markdown');if(full&&state.wsSplit)full.value=wsJoinLang(state.wsSplit.parts,state.wsSplit.lang,e.target.value);const note=root.querySelector('.ws-save-state');if(note){note.textContent='有未保存的修改';note.classList.add('dirty');}}
});
root.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==='Enter'&&e.target.closest('.ws-chat-form')){e.preventDefault();e.target.form?.requestSubmit();return;}
  if(e.key==='Escape'&&state.wsChatOpen&&root.querySelector('.ws-drawer.open')){actions['ws-chat']();return;}
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'&&e.target.matches('.ws-textarea, .latex-source')){e.preventDefault();e.target.form?.requestSubmit();}
});
root.addEventListener('click',e=>{root.querySelectorAll('.ws-menu[open]').forEach(menu=>{if(!menu.contains(e.target)||e.target.closest('.ws-menu-pop button'))menu.open=false;});});
