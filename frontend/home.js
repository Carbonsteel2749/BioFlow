'use strict';
const homeDraft={text:'',mode:'auto',project:'',files:[],run:'',busy:false};
const homeSignout=actions.signout;
actions.signout=async()=>{Object.assign(homeDraft,{text:'',mode:'auto',project:'',files:[],run:'',busy:false});state.page='home';await homeSignout();};
const modeLabels={auto:'自动判断',analysis:'分析',charts:'图表',writing:'写作'};
const assetLabels={literature:'文献知识库',data:'测序数据与表格',results:'分析结果',charts:'图表',drafts:'Draft'};
const assetStatus={ready:'可用',indexed:'已索引',completed:'已完成',processing:'索引中',failed:'索引失败',stored:'已保存，等待服务接收',indexing:'已提交索引',empty:'空文件 · 0 B',missing:'文件缺失',integrity_error:'校验失败',draft:'草稿'};
function homeView(){return `<section class="home-studio"><div class="home-mark">BIOFLOW</div><h1>Hi～今天想要分析什么？</h1><form data-form="home-command" class="home-composer"><textarea data-home-command name="instruction" aria-label="研究指令" placeholder="描述研究目标，或添加文献和数据开始一个项目…" required>${esc(homeDraft.text)}</textarea><div class="home-files">${homeDraft.files.map(f=>`<span>▤ ${esc(f.name)} · ${esc(assetLabels[f.kind])}</span>`).join('')}</div><div class="composer-controls"><div class="composer-left"><button type="button" data-action="home-files" class="attach-button" aria-label="添加文件">＋</button><button type="button" data-action="home-mode" class="mode-button">${modeLabels[homeDraft.mode]}⌄</button></div><div class="composer-right"><button type="button" data-action="home-model" class="model-choice">Qwen3-14B ⌄</button><button class="send-button" type="submit" aria-label="发送指令" ${homeDraft.busy?'disabled':''}>↑</button></div></div><input type="file" multiple id="home-file-input" hidden></form><p class="home-footnote">添加文件后自动归入项目资料，文献按项目名称建立知识库。</p><div id="home-run"></div></section>`;}
guestView=homeView;
views.home=async()=>{if(homeDraft.run)setTimeout(()=>pollHomeRun().catch(e=>notify(e.message)),0);return homeView();};
root.addEventListener('input',e=>{if(e.target.matches('[data-home-command]'))homeDraft.text=e.target.value;});
async function ensureHomeProject(){
 if(homeDraft.run){homeDraft.project='';homeDraft.files=[];homeDraft.run='';}
 if(homeDraft.project&&state.projects.some(p=>p.id===homeDraft.project)){state.project=homeDraft.project;return;}
 const name=(homeDraft.text.trim()||homeDraft.files[0]?.name||'新研究项目').replace(/\s+/g,' ').slice(0,48);
 const p=await api('/api/projects','POST',{name,description:homeDraft.text.slice(0,2000)});
 homeDraft.project=p.id;state.project=p.id;state.projects.push(p);chrome();
}
actions['home-files']=()=>$('#home-file-input').click();
actions['home-model']=()=>modal('选择模型','<div class="model-option"><b>Qwen3-14B</b><span class="tag">当前模型</span></div>');
actions['home-mode']=()=>modal('选择研究任务',`<div class="mode-options">${Object.entries(modeLabels).map(([v,label])=>button(label,'home-select-mode',`data-mode="${v}" class="${homeDraft.mode===v?'selected':''}"`)).join('')}</div><p class="muted">选择功能后进入对应工作台；自动判断由 Qwen3-14B 解析指令。</p>`);
actions['home-select-mode']=async el=>{
 homeDraft.mode=el.dataset.mode;$('.overlay').hidden=true;
 if(homeDraft.mode==='auto'){await render();return;}
 await ensureHomeProject();state.page=homeDraft.mode;await render();
};
forms['home-command']=async f=>{
 homeDraft.text=f.elements.instruction.value;homeDraft.busy=true;
 try{await ensureHomeProject();const run=await api(pa('/assistant'),'POST',{instruction:homeDraft.text,mode:homeDraft.mode,model:'qwen3:14b'});homeDraft.run=run.id;await render();await pollHomeRun();}finally{homeDraft.busy=false;}
};
async function pollHomeRun(){
 if(!homeDraft.run||state.page!=='home'||!bioflowUser)return;
 const run=await api(`/api/projects/${id(homeDraft.project)}/assistant/${id(homeDraft.run)}`);
 const target=$('#home-run');if(!target)return;
 const active=['queued','planning','running'].includes(run.status);
 target.innerHTML=`<div class="command-result"><span class="tag">${({queued:'已保存',planning:'正在理解指令',running:'正在执行',completed:'已提交执行',waiting_model:'等待模型连接',waiting_input:'等待资料',failed:'执行失败'})[run.status]||esc(run.status)}</span><p>${esc(typeof run.result.message==='string'?run.result.message:JSON.stringify(run.result.message||run.plan.summary||'正在处理研究指令…'))}</p>${button('进入项目','home-open-project',`data-mode="${esc(run.plan.mode||'overview')}"`)}${!active&&run.status!=='completed'?button('重试指令','home-retry'):''}</div>`;
 if(active)pollTimer=setTimeout(()=>pollHomeRun().catch(e=>notify(e.message)),2500);
}
actions['home-open-project']=async el=>{state.project=homeDraft.project;await navigate(el.dataset.mode);};
actions['home-retry']=async()=>{await api(`/api/projects/${id(homeDraft.project)}/assistant/${id(homeDraft.run)}/retry`,'POST',{});await pollHomeRun();};
actions.password=()=>modal('修改密码',`<form data-form="password" class="password-form"><label>当前密码<input type="password" name="current" autocomplete="current-password" required></label><label>新密码<input type="password" name="new" minlength="8" autocomplete="new-password" required></label><label>确认新密码<input type="password" name="confirm" minlength="8" autocomplete="new-password" required></label><button class="primary">保存新密码</button></form>`);
forms.password=async f=>{const d=new FormData(f);if(d.get('new')!==d.get('confirm'))throw Error('两次输入的新密码不同');const result=await api('/api/auth/password','POST',{current_password:d.get('current'),new_password:d.get('new')});bioflowUser=result.user;bioflowCsrf=result.user.csrf;$('.overlay').hidden=true;notify('密码已更新，其他登录会话已退出。');};
const originalLiteratureView=views.assets;
state.assetKind='literature';state.assetPage=0;state.assetReady=true;
function assetCard(a){
 const preview=a.artifact_id&&a.status==='ready'?`/api/projects/${id(state.project)}/artifacts/${id(a.artifact_id)}/preview`:null;
 const image=preview&&a.media_type?.startsWith('image/');
 return `<article class="asset-card">${image?`<a href="${preview}" target="_blank" rel="noopener"><img src="${preview}" loading="lazy" alt="${esc(a.name)}"></a>`:''}<div class="asset-card-body"><h3 title="${esc(a.name)}">${esc(a.name)}</h3><p>${esc(a.source||'项目资料')}</p><span class="tag ${['empty','missing','integrity_error'].includes(a.status)?'offline':''}">${esc(assetStatus[a.status]||a.status)}</span><span class="asset-size">${a.size_bytes?Math.round(a.size_bytes/1024).toLocaleString()+' KB':'0 B'}</span><div class="row">${preview?`<a class="asset-link" href="${preview}" target="_blank" rel="noopener">查看</a>`:''}${a.url?`<a class="asset-link" href="${esc(a.url)}" target="_blank" rel="noopener">下载</a>`:''}${a.status==='stored'?button('重试入库','asset-retry',`data-id="${esc(a.id)}"`):''}${a.session_id?button('打开稿件','asset-draft',`data-session="${esc(a.session_id)}"`):''}</div></div></article>`;
}
async function catalogueView(kind,title){
 const data=(await api(pa('/assets'))).items;const category=data.filter(a=>a.kind===kind);
 const good=a=>!['empty','missing','integrity_error'].includes(a.status);
 const visible=category.filter(a=>!state.assetReady||good(a));const pages=Math.max(1,Math.ceil(visible.length/24));state.assetPage=Math.min(state.assetPage,pages-1);
 return head(title)+`<div class="asset-toolbar"><div class="tabs">${Object.entries(assetLabels).map(([k,label])=>button(`${label} · ${data.filter(a=>a.kind===k).length}`,'asset-kind',`data-kind="${k}" class="${kind===k?'selected':''}"`)).join('')}</div><div class="row">${button('＋ 添加文件','asset-upload')}${kind==='literature'?button('文献检索与问答','asset-literature'):''}${kind==='data'?button('双端数据上传','asset-sequencing'):''}${kind==='charts'?button('新建绘图','asset-chart-editor','class="primary"'):''}</div></div><input type="file" id="project-file-input" multiple hidden><div class="asset-summary"><span>${category.filter(good).length} 项有内容 · ${category.filter(a=>!good(a)).length} 项内容缺失</span>${button(state.assetReady?'显示全部记录':'仅显示可用文件','asset-toggle')}</div><div class="asset-grid">${visible.slice(state.assetPage*24,(state.assetPage+1)*24).map(assetCard).join('')||'<div class="empty-state"><h2>暂无可用文件</h2><p>已有记录的空文件可切换“显示全部记录”查看。</p></div>'}</div><div class="asset-pagination">${state.assetPage>0?button('上一页','asset-prev'):''}<span>${state.assetPage+1} / ${pages}</span>${state.assetPage+1<pages?button('下一页','asset-next'):''}</div>`;
}
views.assets=()=>state.assetLegacy?originalLiteratureView():catalogueView(state.assetKind,'项目资料');
const chartEditor=views.charts;
views.charts=()=>state.chartEditor?chartEditor():catalogueView('charts','项目图表');
actions['asset-kind']=async el=>{state.assetKind=el.dataset.kind;state.assetPage=0;state.assetReady=state.assetKind!=='data';state.assetLegacy=false;state.chartEditor=false;state.page='assets';await render();};
actions['asset-toggle']=async()=>{state.assetReady=!state.assetReady;state.assetPage=0;await render();};
actions['asset-prev']=async()=>{state.assetPage--;await render();};
actions['asset-next']=async()=>{state.assetPage++;await render();};
actions['asset-upload']=()=>$('#project-file-input').click();
actions['asset-literature']=async()=>{state.assetLegacy=true;state.filter='';await render();};
actions['asset-sequencing']=async()=>{state.assetLegacy=true;state.filter='sequencing';await render();};
actions['asset-chart-editor']=async()=>{state.chartEditor=true;state.page='charts';await render();};
actions['asset-draft']=async el=>{state.session=el.dataset.session;await navigate('writing');};
root.addEventListener('click',e=>{const el=e.target.closest('[data-page="assets"],[data-page="charts"]');if(el){state.assetLegacy=false;state.chartEditor=false;state.assetPage=0;if(el.dataset.page==='charts')state.assetReady=true;}},true);
const overviewWithAssets=views.overview;
views.overview=async()=>{
 const html=await overviewWithAssets();const runs=await api(pa('/assistant'));
 return html+(runs.length?`<h2 class="command-history-heading">研究指令</h2>${runs.map(r=>`<article class="command-result"><b>${esc(r.instruction)}</b><p>${esc(typeof r.result.message==='string'?r.result.message:r.plan.summary||r.status)}</p>${['waiting_model','waiting_input','failed'].includes(r.status)?button('重试指令','project-retry',`data-run="${esc(r.id)}"`):''}</article>`).join('')}`:'');
};
actions['project-retry']=async el=>{await api(pa('/assistant/'+id(el.dataset.run)+'/retry'),'POST',{});notify('已重新提交指令');await render();};

actions['asset-retry']=async el=>{await api(pa('/assets/'+id(el.dataset.id)+'/retry'),'POST',{});await render();notify('文件已重新提交入库');};
const categorizedAssets=views.assets;
views.assets=async()=>{
 const html=await categorizedAssets();if(state.assetLegacy||state.assetKind!=='data')return html;
 const ids=await api(pa('/links/dataset'));
 const results=await Promise.allSettled(ids.map(d=>service('analysis','/api/datasets/'+id(d))));
 const datasets=results.filter(r=>r.status==='fulfilled').map(r=>r.value);
 if(!datasets.length)return html;
 const section=`<section class="dataset-summary"><h2>已关联的数据集</h2>${datasets.map(d=>`<article class="box"><h3>${esc(d.tasks?.[0]?.name||d.name)}</h3><p>${d.sample_count} 个样本 · ${esc(d.samples?.map(s=>s.sample_id).join('、')||'')}</p><p>${esc(d.reason||'配对文件可用')}</p>${button('查看样本','asset-dataset',`data-id="${esc(d.id)}"`)}</article>`).join('')}</section>`;
 return html.replace('<div class="asset-summary">',section+'<div class="asset-summary">');
};
actions['asset-dataset']=async el=>{const d=await api(pa('/datasets/'+id(el.dataset.id)+'/files'));modal('数据集样本',`<p>${esc(d.tasks?.[0]?.name||d.name)}</p><p>${esc(d.reason||'配对文件可用')}</p><div class="tablewrap"><table><thead><tr><th>样本</th><th>Read 1</th><th>Read 2</th></tr></thead><tbody>${(d.samples||[]).map(s=>`<tr><td>${esc(s.sample_id)}</td>${['read1','read2'].map(k=>`<td>${s[k].size_bytes?Math.round(s[k].size_bytes/1024)+' KB':'0 B'} · ${s[k].availability==='available'?'可用':'内容缺失或变化'}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);};
