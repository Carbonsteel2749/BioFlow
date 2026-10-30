/* Executable releases are always loaded from the analysis engine through the owned gateway. */
(function(){
 'use strict';
 const M=window.BioFlowUnified,U=window.BioFlowRunUI,cache=new Map();let ticket=0,historyIntent=0,pendingRun=null;
 const key=()=>JSON.stringify([bioflowUser?.id,state.project]);
 function currentState(){if(!cache.has(key()))cache.set(key(),{templates:[],registry:[],datasets:[],params:{},selected:null,node:null,history:null,runs:[],name:'',dataset:'',autoRepair:false});return cache.get(key());}
 const endpoint=s=>pa('/analysis/workflows'+s);
 const definition=(s,n)=>(s.history?.snapshot?.parameter_registry?.nodes||s.registry).find(d=>d.type===n.type)||{label:n.type,parameters_schema:{}};
 const graph=s=>s.history?M.history(s.history):s.selected?.record.workflow;
 U.bind({key,current:currentState,read:task=>api(endpoint('/runs/'+id(task)))});
 function capture(){const s=currentState(),form=$('[data-form="unified-run"]');if(!form||s.history)return;
  s.name=form.elements.namedItem('name')?.value??s.name;s.dataset=form.elements.namedItem('dataset_id')?.value??s.dataset;s.autoRepair=!!form.elements.namedItem('autoRepair')?.checked;
  form.querySelectorAll('[data-unified-param]').forEach(el=>{const schema=definition(s,graph(s).nodes.find(n=>n.id===el.dataset.node)).parameters_schema[el.dataset.unifiedParam];let value=el.type==='checkbox'?el.checked:el.value;if(schema.enum)value=schema.enum.find(x=>String(x)===String(value));else if(['number','integer'].includes(schema.type))value=el.value===''?'':Number(el.value);s.params[el.dataset.node][el.dataset.unifiedParam]=value;});
 }
 function controls(s,n,readonly=false){const values=readonly?n.parameters:s.params[n.id],schema=definition(s,n).parameters_schema||{};const properties=readonly?Object.fromEntries(Object.keys(values||{}).map(k=>[k,schema[k]||{title:k,type:typeof values[k]}])):schema;return Object.entries(properties).map(([k,p])=>{
  const v=values?.[k]??(readonly?'未记录':p.default??''),a=`data-unified-param="${esc(k)}" data-node="${esc(n.id)}" ${readonly?'disabled':''}`;
  let control=p.type==='boolean'?`<input type="checkbox" ${a} ${v?'checked':''}>`:p.enum?`<select ${a}>${p.enum.map(x=>`<option value="${esc(x)}" ${String(x)===String(v)?'selected':''}>${esc(x)}</option>`).join('')}</select>`:`<input ${a} type="${['number','integer'].includes(p.type)?'number':'text'}" value="${esc(v)}" ${p.minimum!==undefined?`min="${p.minimum}"`:''} ${p.maximum!==undefined?`max="${p.maximum}"`:''} step="${p.type==='integer'?'1':'any'}">`;
  return `<label class="wb-field"><span>${esc(p.title||k)} · ${esc(k)}</span>${control}<small>${esc(p.description||'已登记参数')}${p.unit?' · '+esc(p.unit):''}</small></label>`;
 }).join('')||'<p class="wb-muted">此节点没有可调整的登记参数。</p>';}
 function choose(s,r){s.selected=r;s.params=M.parameters(r);s.node=r.record.workflow.nodes[0]?.id;s.history=null;}
 const previous=views.analysis;
 views.analysis=async()=>{
  // Existing standard task inspection remains available, using its historical state.
  if(wbState().mode==='run')return previous();
  const k=key(),seq=++ticket,s=currentState();
  let runs={total:s.runsTotal||0};
  if(!s.history){const [releases,registry,data,list,limits]=await Promise.all([api(endpoint('')),service('analysis','/api/workflows/registry'),api(pa('/analysis/datasets')),api(endpoint('/runs?offset='+(s.offset||0))),service('analysis','/api/resources/limits').catch(()=>null)]);
  if(k!==key()||seq!==ticket)return '';
  runs=list;Object.assign(s,{templates:releases.items||[],registry:registry.nodes||[],datasets:data.items||[],runs:runs.items||[],runsTotal:runs.total||0});
  for(const d of s.registry)if(d.parameters_schema?.threads&&Number.isFinite(limits?.cpu?.max_threads))d.parameters_schema.threads.maximum=Math.min(d.parameters_schema.threads.maximum||Infinity,limits.cpu.max_threads);
  }
  if(!s.selected&&s.templates.length)choose(s,s.templates[0]);
  const g=graph(s),n=g?.nodes.find(x=>x.id===s.node)||g?.nodes[0];
  const title=s.history?'历史流程 · 固定运行快照':s.selected?.record.name||'请选择流程';
  const live=s.history&&g?U.view(s,g):null;
  return `<div class="wb-heading"><div><h1>数据分析工作台</h1><p>设计流程、配置节点，跟踪每一步真实分析。</p></div>${button('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="2" y="3" width="6" height="6" rx="1"/><rect x="16" y="15" width="6" height="6" rx="1"/><path d="M5 9v9h11 M8 6h11v9"/></svg><span>设计流程</span>','workflows','class="uw-design-button"')}</div>
  <div class="wb-toolbar"><label>已发布流程<select data-unified-template ${s.history?'disabled':''}>${s.templates.map(r=>`<option value="${esc(r.template_id)}" ${r.template_id===s.selected?.template_id?'selected':''}>${esc(r.record.name)} · v${r.version}</option>`).join('')}</select></label>${button('刷新已发布版本','unified-refresh')}${s.history?button('返回新建分析','unified-new'):''}</div>
  <p class="wb-callout">${esc(title)}${s.history?' · 此视图不读取最新模板，也不会改写原任务。':' · 修改设计草稿不会影响这里；发布后刷新可载入新版本。'} 图表、通路视图和解读节点属于结果展示，不另行执行未接入的模型或绘图动作。</p>
  <div ${live?'data-uw-live data-refreshes="0"':''}>
  ${live?`<section class="uw-run-summary" aria-label="任务运行总览"><div data-uw-summary>${U.summary(live)}</div><div data-uw-live-error role="status">${esc(s.liveError||'')}</div>${button('刷新运行状态','unified-live-refresh')}<small> · 活动任务每 4 秒自动刷新，切换页面或隐藏窗口时暂停。</small></section>`:''}
  ${g?`<div class="wb-workspace uw-workspace"><nav class="uw-stage-rail" data-uw-track aria-label="统一分析流程">${U.stage(s,g,live||U.view(s,g),!!s.liveError)}</nav><form class="wb-main-panel" data-form="unified-run"><div class="wb-panel-body">
  <h2 data-uw-node-title>${esc(n?definition(s,n).label:'流程')}</h2><p data-uw-node-caption>${esc(n?.id||'')} · 每个节点实例单独保存参数。</p>
  ${s.history?'':`<label class="wb-field">任务名称<input name="name" maxlength="120" required value="${esc(s.name)}"></label><label class="wb-field">项目数据集<select name="dataset_id" required><option value="">请选择数据集</option>${s.datasets.map(d=>`<option value="${esc(d.id)}" ${d.id===s.dataset?'selected':''}>${esc(d.name)} · ${esc(d.sample_count)} 个样本</option>`).join('')}</select></label>`}
  ${s.history?`<div data-uw-node-run>${U.detail(live,s.node)}</div><details><summary>本节点参数快照（只读）</summary><div data-uw-node-parameters>${n?controls(s,n,true):''}</div></details>${button('运行控制','unified-control')}`:n?controls(s,n):''}
  ${s.history?'':`<label class="wb-toggle"><input type="checkbox" name="autoRepair" ${s.autoRepair?'checked':''}><span>启用受限 AI 自动修复（本任务默认关闭）</span></label><p class="wb-muted">提交后后端再次检查图结构、参数、数据库和资源；未知参数不会静默忽略。</p><button type="submit" class="primary" ${s.submitting||s.createdTask||s.uncertain?'disabled':''}>${s.submitting?'正在提交…':'确认并创建分析任务'}</button><div data-unified-submit-status role="status" aria-live="polite">${esc(s.feedback||'')}</div>`}
  </div></form></div>`:'<p>暂无可用流程，请到设计器发布。</p>'}
  </div><section class="wb-history"><h2>项目流程任务</h2>${s.runs.map(r=>`<article class="wb-task-card"><div class="wb-task-copy"><h3>${esc(r.name||r.task_id)}</h3><p>${esc(M.status(r.status).label)}</p></div>${button('查看进度与参数','unified-history',`data-id="${esc(r.task_id)}"`)}</article>`).join('')||'<p>暂无流程任务。</p>'}</section>
  <div class="row">${(s.offset||0)>0?button('上一页流程任务','unified-page','data-delta="-20"'):''}${(s.offset||0)+20<(runs.total||0)?button('下一页流程任务','unified-page','data-delta="20"'):''}</div>
  ${s.history?'':`<details><summary>查看原标准流程历史任务</summary>${await tasks().then(list=>wbTaskList({...wbState(),list}))}</details>`}`;
 };
 actions['unified-node']=async el=>{capture();const s=currentState();s.node=el.dataset.node;if(s.history){const g=graph(s),n=g.nodes.find(n=>n.id===s.node);if(n)U.select(s,g,definition(s,n).label,controls(s,n,true));return;}await render();};
 actions['unified-page']=async el=>{capture();currentState().offset=Math.max(0,(currentState().offset||0)+Number(el.dataset.delta));await render();};
 actions['unified-refresh']=async()=>{capture();const s=currentState(),k=key();const r=await api(endpoint(''));if(k!==key())return;const chosen=r.items.find(x=>x.template_id===s.selected?.template_id);if(chosen&&chosen.version!==s.selected.version){if(!confirm('有新发布版本。载入后将替换当前节点参数草稿，继续？'))return;choose(s,chosen);}await render();};
 actions['unified-new']=async()=>{++historyIntent;const s=currentState();s.history=null;s.createdTask=null;s.uncertain=false;s.feedback='';wbState().mode='configure';await render();};
 actions['unified-history']=async el=>{capture();const k=key(),s=currentState(),request=++historyIntent,renderVersion=generation;const detail=await api(endpoint('/runs/'+id(el.dataset.id)));if(k!==key()||s!==currentState()||request!==historyIntent||renderVersion!==generation||state.page!=='analysis')return;M.history(detail);s.history=detail;s.liveError='';s.node=detail.snapshot.workflow.nodes[0]?.id;await render();};
 actions['workflow-progress']=async el=>{await actions.close?.();wbState().mode='configure';await actions['unified-history'](el);};
 actions['unified-control']=()=>{const r=currentState().history?.run;if(!r)return;modal('流程运行控制',`<p>${esc(r.name||r.task_id)} · ${esc(M.status(r.status).label)}</p><p>恢复会复用符合条件的节点缓存；不会改变原流程快照。</p>${r.status==='paused'||r.status==='failed'?button('恢复执行','workflow-resume',`data-id="${esc(r.task_id)}"`):''}${['queued','running','paused'].includes(r.status)?button('取消流程','workflow-cancel',`data-id="${esc(r.task_id)}"`):''}`);};
 root.addEventListener('change',async e=>{if(e.target.matches('[data-unified-template]')){capture();const s=currentState();choose(s,s.templates.find(r=>r.template_id===e.target.value));await render();}else if(e.target.closest('[data-form="unified-run"]'))capture();});
 root.addEventListener('input',e=>{if(e.target.closest('[data-form="unified-run"]'))capture();});
 function feedback(s,message,error=false){s.feedback=message;if(currentState()!==s)return;const form=$('[data-form="unified-run"]'),box=form?.querySelector('[data-unified-submit-status]');if(box){box.textContent=message;box.setAttribute('role',error?'alert':'status');box.classList.toggle('wb-submit-error',error);}const submit=form?.querySelector('[type="submit"]');if(submit){submit.disabled=!!(s.submitting||s.createdTask||s.uncertain);submit.textContent=s.submitting?'正在提交…':'确认并创建分析任务';}}
 function closeReview(){const dialog=$('[data-unified-review]');if(dialog){dialog.close();dialog.remove();}pendingRun=null;}
 actions['unified-submit-cancel']=()=>closeReview();
 actions['unified-submit-confirm']=async()=>{
  const pending=pendingRun;if(!pending||pending.key!==key()||pending.state!==currentState()){closeReview();return;}
  const s=pending.state;if(s.submitting||s.createdTask||s.uncertain)return;
  const payload=pending.payload,url=pending.url,k=pending.key;closeReview();s.submitting=true;feedback(s,'正在校验数据库、参数和资源并提交任务，请勿重复点击…');
  try{const run=await api(url,'POST',payload);if(!run.task_id)throw Error('服务未返回任务 ID，请刷新项目流程任务确认提交状态');s.createdTask=run.task_id;if(k!==key())return;feedback(s,'任务已创建：'+run.task_id);await actions['unified-history']({dataset:{id:run.task_id}});}
  catch(error){let message=error.message||'提交失败';try{const parsed=JSON.parse(message);if(typeof parsed==='string')message=parsed;}catch{}if(!s.createdTask&&(error.name==='TypeError'||/未返回任务 ID/.test(message)))s.uncertain=true;feedback(s,(s.createdTask?'任务已创建（'+s.createdTask+'），但读取详情失败，请刷新流程任务；不要重复提交。':s.uncertain?'提交结果尚未确认，请刷新流程任务检查是否已创建，不要重复提交。':'创建未成功：')+' '+message,true);}
  finally{s.submitting=false;if(k===key())feedback(s,s.feedback,!!$('[data-unified-submit-status].wb-submit-error'));}
 };
 forms['unified-run']=async()=>{capture();const s=currentState(),r=s.selected;if(s.history||!r||s.submitting||s.createdTask||s.uncertain)return;if(!s.name.trim()||!s.dataset){feedback(s,'请填写任务名称并选择数据集',true);return;}
  closeReview();const payload=JSON.parse(JSON.stringify({name:s.name,dataset_id:s.dataset,template_id:r.template_id,version:r.version,node_parameters:s.params,auto_repair:s.autoRepair}));
  pendingRun={key:key(),state:s,payload,url:endpoint('/start')};const dataset=s.datasets.find(d=>d.id===s.dataset),dialog=document.createElement('dialog');dialog.dataset.unifiedReview='';dialog.className='wb-submit-review';dialog.setAttribute('aria-label','确认创建分析任务');
  dialog.innerHTML=`<h2>确认创建分析任务</h2><p>任务：${esc(s.name)}</p><p>流程：${esc(r.record.name)} · v${esc(r.version)}</p><p>数据集：${esc(dataset?.name||s.dataset)} · ${esc(dataset?.sample_count??'待校验')} 个样本</p><p>受限 AI 修复：${s.autoRepair?'已开启':'关闭'}</p><p>将提交真实计算任务；数据库或工具缺失时会明确阻止运行。</p><details><summary>核对各节点参数</summary><pre>${esc(JSON.stringify(payload.node_parameters,null,2))}</pre></details><div class="row"><button type="button" data-action="unified-submit-cancel">返回修改</button><button type="button" class="primary" data-action="unified-submit-confirm">确认提交</button></div>`;
  root.append(dialog);dialog.addEventListener('cancel',()=>{pendingRun=null;dialog.remove();});dialog.showModal();
 };
 const oldSignout=actions.signout;actions.signout=async()=>{closeReview();cache.clear();++ticket;++historyIntent;await oldSignout();};
})();
