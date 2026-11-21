/* Section picker for the start-writing dialog: article type, chosen sections and their order.
   Library comes from GET /services/writing/api/section-library. */
const WRITING_SECTION_STATUS={ready:'写作功能已接通',skill_missing:'此章节尚未接通',author_input:'需要作者提供事实'};

function writingSectionDefaults(lib,type){
  const kind=(lib.article_types||[]).find(t=>t.id===type)||(lib.article_types||[]).find(t=>t.id===lib.default_article_type);
  return kind?kind.sections.slice():[];
}

function writingSectionRows(lib,chosen){
  const byId=new Map((lib.sections||[]).map(s=>[s.id,s]));
  const picked=chosen.filter(id=>byId.has(id));
  const rest=(lib.sections||[]).map(s=>s.id).filter(id=>!picked.includes(id));
  const row=(id,on)=>{const s=byId.get(id);const status=s.status||'ready';
    return `<li class="section-row${on?'':' off'}" data-section="${esc(id)}"><label><input type="checkbox" name="section-pick" value="${esc(id)}"${on?' checked':''}> ${esc(s.title_zh)} <span class="caption">${esc(s.title_en)}</span></label>`+
      `<span class="tag section-status ${esc(status)}" title="${esc(s.purpose||'')}">${esc(WRITING_SECTION_STATUS[status]||status)}</span>`+
      `<span class="section-move"><button type="button" data-move="up" aria-label="上移">↑</button><button type="button" data-move="down" aria-label="下移">↓</button></span></li>`;};
  return picked.map(id=>row(id,true)).join('')+rest.map(id=>row(id,false)).join('');
}

function writingSectionsBox(lib){
  const saved=state.writingSectionDraft;
  const type=saved?.article_type||lib.default_article_type||'research_article';
  const chosen=saved?.sections?.length?saved.sections:writingSectionDefaults(lib,type);
  const types=(lib.article_types||[]).map(t=>`<option value="${esc(t.id)}"${t.id===type?' selected':''}>${esc(t.title_zh)}（${esc(t.examples||t.title_en)}）</option>`).join('');
  return `<div class="box section-picker-box"><p>文章类型与章节</p>`+
    `<label>文章类型<select name="article_type">${types}</select></label>`+
    `<p class="caption">勾选章节，用 ↑ ↓ 调整顺序。系统先写结果等有证据的章节，最后写摘要。功能接通不代表材料齐全；伦理、作者贡献、资助等事实请在下方填写，系统不会替作者猜测。</p>`+
    `<ol class="section-picker">${writingSectionRows(lib,chosen)}</ol><p class="caption section-picker-note"></p>`+
    writingAuthorFields(lib)+`</div>`;
}

function writingAuthorFields(lib){
  const saved=state.writingAuthorDraft||{};
  return `<details class="box"><summary>作者事实信息：伦理、贡献、资助、致谢与利益冲突</summary><p class="caption">只填写已确认事实。留空时稿件保留补充提示；填写后按原文放入对应章节，不让模型编造批准编号、经费或贡献。</p>`+
    (lib.sections||[]).filter(s=>s.mode==='author_input'||s.status==='author_input').map(s=>
      `<label>${esc(s.title_zh)}<textarea name="author-${esc(s.id)}" maxlength="6000" rows="2" placeholder="填写已确认内容；也可暂时留空">${esc(saved[s.id]||'')}</textarea></label>`).join('')+`</details>`;
}

function writingAuthorInformation(form){return Object.fromEntries([...form.querySelectorAll('textarea[name^="author-"]')].filter(x=>x.value.trim()).map(x=>[x.name.slice(7),x.value.trim()]));}

function writingSectionChoice(form){
  const list=form.querySelector('.section-picker');
  if(!list)return {};
  const type=form.elements.namedItem('article_type')?.value||'';
  const sections=[...list.querySelectorAll('li.section-row')].filter(li=>li.querySelector('input[name="section-pick"]')?.checked).map(li=>li.dataset.section);
  return {article_type:type,sections};
}

function writingSectionNote(form){
  const note=form.querySelector('.section-picker-note');if(!note)return;
  const rows=[...form.querySelectorAll('.section-picker li.section-row')].filter(li=>li.querySelector('input')?.checked);
  const missing=rows.filter(li=>li.querySelector('.section-status.skill_missing')).length;
  const author=rows.filter(li=>li.querySelector('.section-status.author_input')).length;
  note.textContent=rows.length?`已选 ${rows.length} 个章节`+(missing?`，其中 ${missing} 个尚未接通`:'')+(author?`，${author} 个请提供作者事实信息`:''):'请至少选择一个章节。';
}

/* Figures from the chart module: only completed, saved figures drawn from real data. */
function writingUsableFigures(items){
  return (items||[]).filter(f=>f.status==='completed'&&f.saved&&!(f.source||{}).simulated&&!String((f.source||{}).id||'').startsWith('example:'));
}

function writingFiguresBox(items,pid){
  const usable=writingUsableFigures(items);
  const chosen=new Set(state.writingFigureDraft||[]);
  const rows=usable.map(f=>{const title=(f.parameters||{}).title||f.chart;const src=(f.source||{}).name||'';const task=String((f.source||{}).task_id||'').slice(0,8);
    return `<label class="figure-pick"><input type="checkbox" name="writing-fig" value="${esc(f.id)}"${chosen.has(f.id)?' checked':''}>`+
      `<img alt="" loading="lazy" src="/api/projects/${encodeURIComponent(pid)}/figures/${encodeURIComponent(f.id)}/file/png">`+
      `<span>${esc(title)}<br><span class="caption">${esc(f.chart)} · ${esc(src)}${task?' · 任务 '+esc(task):''}</span></span></label>`;}).join('');
  return `<div class="box"><p>论文图（图表模块）</p><p class="caption">只列出已保存、来自真实数据的图。勾选的图按勾选顺序编号：数据描述论文放在「技术验证」，研究论文放在「结果」，正文中以 (Figure N) 引用。请勾选与所选分析任务一致的图。</p>`+
    `${rows||'<p class="caption">当前项目还没有保存的图。可在「图表」页生成并保存后再来选择。</p>'}</div>`;
}

function writingFigureIds(form){return [...form.querySelectorAll('input[name="writing-fig"]:checked')].map(el=>el.value);}

function bindWritingSections(form,lib){
  const list=form.querySelector('.section-picker');if(!list)return;
  const changed=()=>{delete form.dataset.materialFingerprint;writingSectionNote(form);};
  form.elements.namedItem('article_type')?.addEventListener('change',e=>{
    list.innerHTML=writingSectionRows(lib,writingSectionDefaults(lib,e.target.value));changed();});
  list.addEventListener('change',e=>{const li=e.target.closest('li.section-row');if(li)li.classList.toggle('off',!e.target.checked);changed();});
  list.addEventListener('click',e=>{
    const move=e.target.closest('button[data-move]');if(!move)return;
    e.preventDefault();e.stopPropagation();
    const li=move.closest('li.section-row');
    if(move.dataset.move==='up'&&li.previousElementSibling)list.insertBefore(li,li.previousElementSibling);
    if(move.dataset.move==='down'&&li.nextElementSibling)list.insertBefore(li.nextElementSibling,li);
    changed();});
  writingSectionNote(form);
}
