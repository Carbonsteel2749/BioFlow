/* Executable releases are always loaded from the analysis engine through the owned gateway. */
(function(){
 'use strict';
 const M=window.BioFlowUnified,cache=new Map();let ticket=0;
 const key=()=>JSON.stringify([bioflowUser?.id,state.project]);
 function currentState(){if(!cache.has(key()))cache.set(key(),{templates:[],registry:[],datasets:[],params:{},selected:null,node:null,history:null,runs:[],name:'',dataset:'',autoRepair:false});return cache.get(key());}
 const endpoint=s=>pa('/analysis/workflows'+s);
 const definition=(s,n)=>(s.history?.snapshot?.parameter_registry?.nodes||s.registry).find(d=>d.type===n.type)||{label:n.type,parameters_schema:{}};
 const graph=s=>s.history?M.history(s.history):s.selected?.record.workflow;
 function capture(){const s=currentState(),form=$('[data-form="unified-run"]');if(!form||s.history)return;
  s.name=form.elements.namedItem('name')?.value||s.name;s.dataset=form.elements.namedItem('dataset')?.value||s.dataset;s.autoRepair=!!form.elements.namedItem('autoRepair')?.checked;
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
  const [releases,registry,data,runs,limits]=await Promise.all([api(endpoint('')),service('analysis','/api/workflows/registry'),api(pa('/analysis/datasets')),api(endpoint('/runs?offset='+(s.offset||0))),service('analysis','/api/resources/limits').catch(()=>null)]);
  if(k!==key()||seq!==ticket)return '';
  Object.assign(s,{templates:releases.items||[],registry:registry.nodes||[],datasets:data.items||[],runs:runs.items||[]});
  for(const d of s.registry)if(d.parameters_schema?.threads&&Number.isFinite(limits?.cpu?.max_threads))d.parameters_schema.threads.maximum=Math.min(d.parameters_schema.threads.maximum||Infinity,limits.cpu.max_threads);
  if(!s.selected&&s.templates.length)choose(s,s.templates[0]);
  const g=graph(s),n=g?.nodes.find(x=>x.id===s.node)||g?.nodes[0];
  const title=s.history?'历史流程 · 固定运行快照':s.selected?.record.name||'请选择流程';
  return `<div class="wb-heading"><div><div class="eyebrow">BIOFLOW / ANALYSIS STUDIO</div><h1>数据分析工作台</h1><p>设计器发布 → 选择固定版本 → 节点参数 → 运行快照</p></div>${button('设计 Workflow','workflows')}</div>
  <div class="wb-toolbar"><label>已发布流程<select data-unified-template ${s.history?'disabled':''}>${s.templates.map(r=>`<option value="${esc(r.template_id)}" ${r.template_id===s.selected?.template_id?'selected':''}>${esc(r.record.name)} · v${r.version}</option>`).join('')}</select></label>${button('刷新已发布版本','unified-refresh')}${s.history?button('返回新建分析','unified-new'):''}</div>
  <p class="wb-callout">${esc(title)}${s.history?' · 此视图不读取最新模板，也不会改写原任务。':' · 修改设计草稿不会影响这里；发布后刷新可载入新版本。'} 图表、通路视图和解读节点属于结果展示，不另行执行未接入的模型或绘图动作。</p>
  ${g?`<div class="wb-workspace"><nav class="wb-rail" aria-label="统一分析流程">${g.nodes.map(x=>`<button type="button" class="wb-node ${x.id===n?.id?'selected':''}" data-action="unified-node" data-node="${esc(x.id)}"><span class="wb-node-copy"><strong>${esc(definition(s,x).label)}</strong><small>${esc(x.id)}</small></span></button>`).join('')}</nav><form class="wb-main-panel" data-form="unified-run"><div class="wb-panel-body">
  <h2>${esc(n?definition(s,n).label:'流程')}</h2><p>${esc(n?.id||'')} · 每个节点实例单独保存参数。</p>
  ${s.history?'':`<label class="wb-field">任务名称<input name="name" maxlength="120" required value="${esc(s.name)}"></label><label class="wb-field">项目数据集<select name="dataset" required><option value="">请选择数据集</option>${s.datasets.map(d=>`<option value="${esc(d.id)}" ${d.id===s.dataset?'selected':''}>${esc(d.name)} · ${esc(d.sample_count)} 个样本</option>`).join('')}</select></label>`}
  ${n?controls(s,n,!!s.history):''}
  ${s.history?`<p>状态：${esc(s.history.run.status)}</p><pre>${esc(JSON.stringify(s.history.run.nodes||s.history.run.node_states||[],null,2))}</pre>${button('日志与运行控制','workflow-progress',`data-id="${esc(s.history.run.task_id)}"`)}`:`<label class="wb-toggle"><input type="checkbox" name="autoRepair" ${s.autoRepair?'checked':''}><span>启用受限 AI 自动修复（本任务默认关闭）</span></label><p class="wb-muted">提交后后端再次检查图结构、参数、数据库和资源；未知参数不会静默忽略。</p><button type="submit" class="primary">确认并创建分析任务</button>`}
  </div></form></div>`:'<p>暂无可用流程，请到设计器发布。</p>'}
  <section class="wb-history"><h2>项目流程任务</h2>${s.runs.map(r=>`<article class="wb-task-card"><div class="wb-task-copy"><h3>${esc(r.name||r.task_id)}</h3><p>${esc(r.status)}</p></div>${button('流程与参数快照','unified-history',`data-id="${esc(r.task_id)}"`)}</article>`).join('')||'<p>暂无流程任务。</p>'}</section>
  <div class="row">${(s.offset||0)>0?button('上一页流程任务','unified-page','data-delta="-20"'):''}${(s.offset||0)+20<(runs.total||0)?button('下一页流程任务','unified-page','data-delta="20"'):''}</div>
  <details><summary>查看原标准流程历史任务</summary>${await tasks().then(list=>wbTaskList({...wbState(),list}))}</details>`;
 };
 actions['unified-node']=async el=>{capture();currentState().node=el.dataset.node;await render();};
 actions['unified-page']=async el=>{capture();currentState().offset=Math.max(0,(currentState().offset||0)+Number(el.dataset.delta));await render();};
 actions['unified-refresh']=async()=>{capture();const s=currentState(),k=key();const r=await api(endpoint(''));if(k!==key())return;const chosen=r.items.find(x=>x.template_id===s.selected?.template_id);if(chosen&&chosen.version!==s.selected.version){if(!confirm('有新发布版本。载入后将替换当前节点参数草稿，继续？'))return;choose(s,chosen);}await render();};
 actions['unified-new']=async()=>{currentState().history=null;wbState().mode='configure';await render();};
 actions['unified-history']=async el=>{capture();const k=key(),s=currentState();const detail=await api(endpoint('/runs/'+id(el.dataset.id)));if(k!==key())return;M.history(detail);s.history=detail;s.node=detail.snapshot.workflow.nodes[0]?.id;await render();};
 root.addEventListener('change',async e=>{if(e.target.matches('[data-unified-template]')){capture();const s=currentState();choose(s,s.templates.find(r=>r.template_id===e.target.value));await render();}else if(e.target.closest('[data-form="unified-run"]'))capture();});
 root.addEventListener('input',e=>{if(e.target.closest('[data-form="unified-run"]'))capture();});
 forms['unified-run']=async()=>{capture();const s=currentState(),k=key(),r=s.selected;if(s.history||!r)return;if(!s.name.trim()||!s.dataset)throw Error('请填写任务名称并选择数据集');
  const payload={name:s.name,dataset_id:s.dataset,template_id:r.template_id,version:r.version,node_parameters:s.params,auto_repair:s.autoRepair};
  if(!confirm(`将按 ${r.record.name} v${r.version} 创建真实分析任务，是否继续？`))return;
  const run=await api(endpoint('/start'),'POST',payload);if(k!==key())return;notify('已提交分析任务：'+run.task_id);await actions['unified-history']({dataset:{id:run.task_id}});
 };
 const oldSignout=actions.signout;actions.signout=async()=>{cache.clear();++ticket;await oldSignout();};
})();
