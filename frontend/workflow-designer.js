/* Visual design UI: authenticated reads only. No run/model/save-template POST calls. */
(function(){
 'use strict';
 const M=window.BioFlowDesigner,states=new Map();
 let sequence=0,dialogAction=null,dialogFocus=null,drag=null;
 const key=()=>M.draftKey(bioflowUser?.id||'guest',state.project||'');
 const get=()=>{const k=key();if(!states.has(k))states.set(k,{graph:null,selected:null,catalog:M.catalog(),models:[],datasets:[],ready:false,restored:false,undo:[],redo:[],zoom:.72,scroll:[0,0],panel:'canvas',status:'前端设计预览 · 不启动计算',pending:null,view:'templates',remote:[]});return states.get(k);};
 const icon=(type)=>`<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${({start:'M9 5l11 7-11 7z',fastq_input:'M4 5h16v15H4z M8 2v6 M16 2v6 M8 12h8 M8 16h5',analysis_group:'M4 7V4h6 M14 4h6v6 M20 14v6h-6 M10 20H4v-6',repair_llm:'M8 3h8l2 4h3v10h-3l-2 4H8l-2-4H3V7h3z M9 9v6 M15 9v6',interpret_llm:'M5 4h14v12H9l-4 4z M8 8h8 M8 12h5',fastqc:'M10 3h4 M11 3v7l-6 9h14l-6-9V3 M9 15h6',fastp:'M6 4l12 16 M18 4L6 20 M6 4a3 3 0 1 0 0 6 M18 4a3 3 0 1 1 0 6',host_depletion:'M12 3l8 3v6c0 5-8 9-8 9S4 17 4 12V6z M8 12l3 3 5-6',taxonomy:'M12 3v7 M5 15v-5h14v5 M2 16h6v5H2z M16 16h6v5h-6z',functional_annotation:'M7 2c0 7 10 13 10 20 M17 2C17 9 7 15 7 22 M8 5h8 M9 10h6 M9 14h6 M8 19h8',pathways:'M3 4h6v6H3z M15 14h6v6h-6z M9 7h9v7 M6 10v7h9',report:'M5 3h10l4 4v14H5z M14 3v5h5 M8 12h8 M8 16h8',figures:'M4 3v18h17 M8 16v-4 M13 16V7 M18 16v-6',end:'M6 6h12v12H6z'})[type]||'M7 3c0 7 10 11 10 18 M17 3C17 10 7 14 7 21 M8 7h8 M8 17h8'}"/></svg>`;
 const btn=(text,action,extra='')=>`<button type="button" data-wd-action="${action}" ${extra}>${text}</button>`;
 const selected=()=>get().graph?.nodes.find(n=>n.id===get().selected);
 function status(message){get().status=message;const el=$('.wd-status');if(el)el.textContent=message;}
 function save(){const s=get();if(!s.graph)return;let json;try{json=M.exportJSON(s.graph,{portable:false});}catch(error){status('未保存：'+error.message+'。请修正后再保存。');return;}try{localStorage.setItem(key(),json);status('已保存到此浏览器 · '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})+' · 未提交计算');}catch{status('浏览器存储不可用，请导出 JSON 保留设计，勿关闭页面。');}}
 function checkpoint(){const s=get();if(s.graph){s.undo.push(JSON.stringify(s.graph));if(s.undo.length>60)s.undo.shift();s.redo=[];const u=$('[data-wd-action=undo]'),r=$('[data-wd-action=redo]');if(u)u.disabled=false;if(r)r.disabled=true;}}
 function mutate(fn){checkpoint();fn(get().graph);if(get().selected)get().edge=null;save();draw();}
 const oldChrome=chrome;chrome=function(){oldChrome();root.classList.toggle('wd-mode',state.page==='workflows');};
 names.workflows='Workflow 设计';
 views.workflows=async()=>{
  const s=get(),k=key(),ticket=++sequence;
  if(!s.ready){
   const result=await Promise.allSettled([service('analysis','/api/workflows/registry'),api('/api/models'),api(pa('/analysis/datasets')),api(pa('/analysis/workflows'))]);
   if(k!==key()||ticket!==sequence||state.page!=='workflows')return '';
   s.catalog=M.catalog(result[0].status==='fulfilled'?result[0].value:null);
   s.models=result[1].status==='fulfilled'?items(result[1].value).filter(x=>x.category==='language'):[];
   s.datasets=result[2].status==='fulfilled'?items(result[2].value):[];
   s.remote=result[3].status==='fulfilled'?items(result[3].value).map(r=>({...r.record,visual:r.design,publication:r})):[];
   s.ready=true;s.apiErrors=result.map((r,i)=>r.status==='rejected'?['节点登记','模型列表','项目数据集','服务模板'][i]+'读取失败':null).filter(Boolean);
  }
  if(!s.restored){s.restored=true;try{const raw=localStorage.getItem(k);if(raw){s.graph=M.importJSON(raw,{portable:false,c:s.catalog});const p=JSON.parse(localStorage.getItem(k+'.publication')||'null');if(p&&typeof p.template_id==='string'&&Number.isInteger(p.version)&&p.version>0)s.publication=p;}}catch{status('已保存草稿无法解析；请导入备份或重新选择模板。');}}
  return html(s);
 };
 function html(s){return `<section class="wd-app" aria-label="Workflow 设计工作台">${s.view==='templates'?gallery(s):editor(s)}<input type="file" accept=".json,application/json" data-wd-file hidden><dialog class="wd-dialog" aria-label="流程设计提示"></dialog></section>`;}
 function gallery(s){return `<header class="wd-gallery-head"><div>${btn('← 返回分析','back')}<h1>设计你的分析流程</h1><p>从成熟方法开始，也为自己的研究留出空间。</p></div><span class="wd-preview">前端设计版 · 不执行分析</span></header>
 <div class="wd-gallery-intro"><div class="wd-intro-line"><span>开始</span><i></i><span>数据导入</span><i></i><b>数据分析</b><i></i><span>图表与解读</span></div><p>拖动节点组织流程，点击节点配置参数。修复策略与结果解读使用独立的 LLM。</p></div>
 ${s.graph?`<div class="wd-draft"><div><strong>${esc(s.graph.name)}</strong><p>${s.graph.nodes.length} 个节点 · 此账户 / 此项目的浏览器草稿</p></div>${btn('继续设计','resume','class="primary"')}</div>`:''}
 ${s.apiErrors?.length?`<p class="wd-warning">${esc(s.apiErrors.join('、'))}。仍可使用内置设计模板；模型和数据库可用性未确认。</p>`:''}
 <div class="wd-templates">${M.templates.map(t=>`<button type="button" class="wd-template" data-wd-template="${t.id}"><span class="wd-template-top"><span class="wd-icon">${icon(t.id==='qc'?'fastqc':t.id==='blank'?'analysis_group':t.id==='taxonomy'?'taxonomy':'functional_annotation')}</span><small>${esc(t.tag)}</small></span><strong>${esc(t.name)}</strong><p>${esc(t.description)}</p><span class="wd-template-bottom">${t.id==='blank'?'创建空白流程':'使用此模板'} <b>↗</b></span></button>`).join('')}</div>
 <div class="wd-gallery-bottom"><div><h3>已有自己的流程？</h3><p>导入 BioFlow Workflow v1 JSON；不导入密钥、数据文件或其他项目的数据绑定。</p></div>${btn('导入模板 JSON','import')}</div>
 ${s.remote.length?`<details class="wd-server-templates"><summary>导入分析服务已有模板（${s.remote.length}）</summary><p>复制节点与参数为设计草稿，不改动服务器模板。</p><div>${s.remote.map(t=>`<button type="button" data-wd-server="${esc(t.template_id||t.id)}">${esc(t.name)}</button>`).join('')}</div></details>`:''}
 <p class="wd-status" role="status">${esc(s.status)}</p>`;}
 function editor(s){return `<header class="wd-editor-head"><div class="wd-editor-identity">${btn('← 模板','templates','title="返回模板选择，保留草稿"')}<label class="wd-name">流程名称<input data-wd-name value="${esc(s.graph.name)}" maxlength="120"></label><span class="wd-preview">${s.publication?'基于 v'+s.publication.version:'未发布草稿'}</span></div><div class="wd-actions">${btn('↶','undo',`aria-label="撤销" ${s.undo.length?'':'disabled'}`)}${btn('↷','redo',`aria-label="重做" ${s.redo.length?'':'disabled'}`)}${btn('导入','import')}${btn('导出','export')}${btn('保存草稿','save')}${btn('检查与预览','validate')}${btn('发布流程版本','publish','class="primary"')}</div></header>
 <div class="wd-info"><span>实线传递数据 · 虚线关联修复策略</span><span>仅设计，不调用模型、不启动计算</span></div>
 <div class="wd-mobile-tabs" aria-label="编辑区域">${[['nodes','节点库'],['canvas','画布'],['config','参数']].map(([p,t])=>`<button type="button" data-wd-panel="${p}" aria-pressed="${s.panel===p}">${t}</button>`).join('')}</div>
 <div class="wd-workspace" data-panel="${s.panel}"><aside class="wd-library" aria-label="节点库">${library(s)}</aside><section class="wd-canvas-column" aria-label="流程画布">${canvas(s)}</section><aside class="wd-inspector" aria-label="节点参数">${inspector(s)}</aside></div>
 <footer class="wd-bottom"><span class="wd-status" role="status">${esc(s.status)}</span><span>${s.graph.nodes.filter(n=>n.type!=='analysis_group').length} 节点 / ${s.graph.edges.length} 连接</span></footer>`;}
 function library(s){const categories=['基础','AI','质控','分析','MAG','结果','16S'];return `<div class="wd-library-heading"><h2>添加节点</h2><p>点击添加，拖动编排；登记方法仍需运行预检。</p></div>${categories.filter(category=>Object.values(s.catalog).some(d=>d.category===category)).map(category=>`<details class="wd-category" ${['基础','AI','质控','分析','结果'].includes(category)?'open':''}><summary>${category}</summary>${Object.entries(s.catalog).filter(([t,d])=>d.category===category&&!['start','fastq_input','analysis_group','end'].includes(t)).map(([type,d])=>`<button type="button" data-wd-add="${type}" ${type==='repair_llm'&&s.graph.nodes.some(n=>n.type===type)?'disabled':''}><span class="wd-icon wd-cat-${category==='AI'?'ai':'tool'}">${icon(type)}</span><span>${esc(d.label)}<small>${esc(d.tool)}</small></span><b>+</b></button>`).join('')}</details>`).join('')}
 <details class="wd-outline" open><summary>画布节点</summary>${s.graph.nodes.filter(n=>n.type!=='analysis_group').map(n=>`<button type="button" data-wd-select="${n.id}" class="${n.id===s.selected?'selected':''}">${esc(n.label)}</button>`).join('')}</details>`;}
 function dimensions(g){return {width:Math.max(1400,...g.nodes.map(n=>n.x+300)),height:Math.max(800,...g.nodes.map(n=>n.y+230))};}
 function endpoint(n,port,direction,c){if(n.type==='analysis_group')return {x:610,y:75};const ports=c[n.type][direction==='out'?'outputs':'inputs'];const idx=ports.findIndex(p=>p.id===port);return {x:n.x+(direction==='out'?222:0),y:n.y+86+idx*24};}
 function edges(s){return s.graph.edges.map(e=>{const a=s.graph.nodes.find(n=>n.id===e.source),b=s.graph.nodes.find(n=>n.id===e.target);if(!a||!b)return '';const p=endpoint(a,e.sourcePort,'out',s.catalog),q=endpoint(b,e.targetPort,'in',s.catalog);const bend=Math.max(45,Math.abs(q.x-p.x)*.45);return `<g data-edge="${e.id}" class="wd-edge ${e.kind!=='data'?'wd-edge-policy':''} ${s.edge===e.id?'selected':''}"><path class="wd-edge-hit" data-wd-edge="${e.id}" d="M${p.x} ${p.y} C${p.x+bend} ${p.y},${q.x-bend} ${q.y},${q.x} ${q.y}"/><path class="wd-edge-line" d="M${p.x} ${p.y} C${p.x+bend} ${p.y},${q.x-bend} ${q.y},${q.x} ${q.y}"/></g>`;}).join('');}
 function modelOptions(s,value){return `<option value="">选择语言模型</option>${value&&!s.models.some(m=>m.id===value)?`<option value="${esc(value)}" selected>${esc(value)} · 当前不可用（保留设计）</option>`:''}${s.models.map(m=>`<option value="${esc(m.id)}" ${m.id===value?'selected':''} ${m.available===false?'disabled':''}>${esc(m.name)} · ${esc(m.provider||'已登记')}${m.available===false?' · 不可用':''}</option>`).join('')}`;}
 function syncModel(n,s){const node=root.querySelector(`.wd-node[data-node="${n.id}"]`),inline=node?.querySelector('[data-wd-inline-model]');if(inline)inline.innerHTML=modelOptions(s,n.parameters.model_id);if(s.selected===n.id){const field=$('[data-wd-param=model_id]');if(field)field.innerHTML=modelOptions(s,n.parameters.model_id);}if(node)node.querySelector('.wd-node-sub').textContent=n.type==='repair_llm'&&!n.parameters.enabled?'自动修复未启用':s.models.find(m=>m.id===n.parameters.model_id)?.name||'请选择语言模型';}
 function card(n,s){const d=s.catalog[n.type];const port=(p,dir)=>`<button type="button" class="wd-port ${dir}" data-wd-${dir}="${esc(p.id)}" data-node="${n.id}" aria-label="${esc(n.label)} ${dir==='out'?'输出':'输入'} ${esc(p.id)}" title="${esc((p.data_type||p.accepted_data_types?.join(', '))||p.id)}"><i></i><span>${esc(p.id)}</span></button>`;
  const sub=['repair_llm','interpret_llm'].includes(n.type)?(n.type==='repair_llm'&&!n.parameters.enabled?'自动修复未启用':s.models.find(m=>m.id===n.parameters.model_id)?.name||'请选择语言模型'):n.type==='fastq_input'?(s.datasets.find(x=>x.id===n.parameters.dataset_id)?.name||'选择项目数据集'):d.tool;
  return `<article class="wd-node ${n.id===s.selected?'selected':''} ${d.category==='AI'?'wd-ai-node':''}" data-node="${n.id}" data-type="${n.type}" style="left:${n.x}px;top:${n.y}px"><button type="button" class="wd-node-title" data-wd-select="${n.id}" title="拖动节点；选中后可用 Alt + 方向键移动"><span class="wd-icon">${icon(n.type)}</span><strong>${esc(n.label)}</strong><span>⋮</span></button><div class="wd-node-sub">${esc(sub)}</div><div class="wd-node-ports"><div>${d.inputs.map(p=>port(p,'in')).join('')}</div><div>${d.outputs.map(p=>port(p,'out')).join('')}</div></div>${['repair_llm','interpret_llm'].includes(n.type)?`<label class="uw-model-design">设计模型<select data-wd-inline-model="${n.id}" aria-label="${esc(n.label)} 设计模型">${modelOptions(s,n.parameters.model_id)}</select><small>设计选择；实际模型由后端配置，尚未切换。</small></label>`:''}${d.category==='16S'?'<span class="wd-node-note">设计节点 · 后端待接入</span>':''}</article>`;
 }
 function canvas(s){const b=M.groupBounds(s.graph),size=dimensions(s.graph),group=s.graph.nodes.find(n=>n.type==='analysis_group');return `<div class="wd-viewport" tabindex="0" aria-label="可缩放拖动的流程画布"><div class="wd-space" style="width:${size.width*s.zoom}px;height:${size.height*s.zoom}px"><div class="wd-world" style="width:${size.width}px;height:${size.height}px;transform:scale(${s.zoom})">${group?`<div class="wd-analysis-group" style="left:${b.x}px;top:${b.y}px;width:${b.width}px;height:${b.height}px"><header><span>${icon('analysis_group')} 数据分析</span><button type="button" data-wd-in="policy" data-node="${group.id}" aria-label="数据分析 输入 policy">◌ 修复策略</button></header></div>`:''}<svg class="wd-edges" width="${size.width}" height="${size.height}" aria-hidden="true">${edges(s)}</svg>${s.graph.nodes.filter(n=>n.type!=='analysis_group').map(n=>card(n,s)).join('')}</div></div></div><div class="wd-canvas-tools"><div>${btn('−','zoom-out','aria-label="缩小画布"')}<span>${Math.round(s.zoom*100)}%</span>${btn('+','zoom-in','aria-label="放大画布"')}${btn('适应画布','fit')}${btn('自动整理','arrange')}</div><small>拖动节点移动 · 拖动空白平移 · 点击端口连线 · Esc 取消</small></div>`;}
 function field(n,k,p,s){const v=n.parameters[k]??p.default??'',attrs=`data-wd-param="${esc(k)}"`;
  let control;
  if(k==='model_id'){control=`<select ${attrs}>${modelOptions(s,v)}</select><small>仅保存设计选择；实际模型由后端配置，当前不按画布选择切换。</small>`;}
  else if(k==='dataset_id')control=`<select ${attrs}><option value="">选择当前项目数据集</option>${s.datasets.map(d=>`<option value="${esc(d.id)}" ${d.id===v?'selected':''} ${!['available','not_checked',undefined].includes(d.status)?'disabled':''}>${esc(d.name)} · ${d.sample_count??'?'} 样本</option>`).join('')}</select>`;
  else if(p.type==='boolean')control=`<input ${attrs} type="checkbox" ${v?'checked':''}>`;
  else if(p.enum)control=`<select ${attrs}>${p.enum.map(o=>`<option value="${esc(o)}" ${o===v?'selected':''}>${esc(({task_copy:'仅本任务脚本副本',failed_node:'从失败节点恢复',composition:'物种组成',diversity:'多样性（需样本条件）',functional:'功能概况',overview:'质量概览'})[o]||o)}</option>`).join('')}</select>`;
  else if(k==='prompt'||k==='metadata_note')control=`<textarea ${attrs} rows="${k==='prompt'?6:3}" maxlength="12000">${esc(v)}</textarea>`;
  else control=`<input ${attrs} type="${['integer','number'].includes(p.type)?'number':'text'}" value="${esc(v)}" ${p.type==='number'?'step="any"':''} ${p.minimum!==undefined?`min="${p.minimum}"`:''} ${p.maximum!==undefined?`max="${p.maximum}"`:''} maxlength="12000">`;
  const range=p.minimum!==undefined||p.maximum!==undefined?`范围 ${p.minimum??'不限'}–${p.maximum??'未登记上限'}`:'';
  return `<label class="wd-field ${p.type==='boolean'?'wd-check':''}"><span>${esc(p.title||k)} ${p.unit?`<em>${esc(p.unit)}</em>`:''}</span>${control}<small>${esc(p.description||'')}</small><small class="wd-param-meta">${esc(k)}${range?' · '+esc(range):''} · 默认 ${esc(p.default===false?'关闭':p.default===true?'开启':String(p.default??'未设定').slice(0,50)||'未设定')}</small></label>`;
 }
 function inspector(s){const edge=s.graph.edges.find(e=>e.id===s.edge);
  if(edge){const source=s.graph.nodes.find(n=>n.id===edge.source),target=s.graph.nodes.find(n=>n.id===edge.target);return `<div class="wd-inspector-head"><div><h2>连线设置</h2><small>仅影响当前设计草稿</small></div>${btn('×','clear-selection','aria-label="关闭连线设置"')}</div><div class="wd-inspector-body"><h3>${esc(source?.label||edge.source)} → ${esc(target?.label||edge.target)}</h3><dl><dt>输出端口</dt><dd>${esc(edge.sourcePort)}</dd><dt>输入端口</dt><dd>${esc(edge.targetPort)}</dd></dl><p>删除仅断开这条连线，不会删除节点或历史任务。可点击顶部“撤销”恢复。</p>${btn('删除连线','delete-edge',`data-edge-id="${esc(edge.id)}" class="wd-danger"`)}<p class="wd-hint">选中连线后，也可按 Delete 或 Backspace 删除；输入参数时不会触发。</p></div>`;}
  const n=s.graph.nodes.find(n=>n.id===s.selected);
  if(!n)return `<div class="wd-inspector-empty">${icon('analysis_group')}<h2>点选节点，配置分析</h2><p>每个节点独立保存参数。右侧展示参数作用、默认值与输入输出。</p><h3>连接方式</h3><p>先点输出端口，再点输入端口；也可以拖线连接。</p><p>AI 修复是可选策略；分析后的解读 LLM 独立配置。</p></div>`;
  const d=s.catalog[n.type],basic=Object.entries(d.parameters).filter(([,p])=>!p.advanced),advanced=Object.entries(d.parameters).filter(([,p])=>p.advanced);
  return `<div class="wd-inspector-head"><span class="wd-icon">${icon(n.type)}</span><div><h2>${esc(n.label)}</h2><small>${esc(d.tool)}</small></div>${btn('×','clear-selection','aria-label="关闭节点参数"')}</div><div class="wd-inspector-body"><p class="wd-description">${esc(d.description)}</p>${d.design?'<p class="wd-warning">设计 / 结果视图节点，不作为独立计算方法执行。</p>':d.registered?'<p class="wd-hint">执行方法已登记；参数与端口来自后端。发布后仍须通过工具、数据库及资源预检。</p>':'<p class="wd-warning">未取得后端登记信息；不能据此确认方法可执行。</p>'}${d.category==='MAG'?`<div class="wd-warning">MAG 分支通常需要较多 CPU、内存和磁盘，请勿作为所有任务的默认必经流程。${d.requirements?.tools?.length?`<p>工具：${esc(d.requirements.tools.join('、'))}</p>`:''}${d.requirements?.databases?.length?`<p>数据库：${esc(d.requirements.databases.join('、'))}</p>`:''}</div>`:''}
  ${M.roots.includes(n.type)?`<p class="wd-hint">输入绑定在分析配置中按本节点 ID 独立选择；可移植流程不携带账户数据。${n.type==='contigs_input'?'分箱还须连接同队列 clean / host_removed reads 伴随输入。':''}${n.type==='mag_input'?'外部 MAG 仅支持已登记注释方法，不支持外部 MAG 定量。':''}</p>${button('导入分析数据','input-datasets','type="button"')}`:''}
  <label class="wd-field"><span>节点名称</span><input data-wd-label value="${esc(n.label)}" maxlength="80"><small>节点 ID：${esc(n.id)}，重复节点互不覆盖。</small></label>
  ${basic.map(([k,p])=>field(n,k,p,s)).join('')}${advanced.length?`<details class="wd-advanced"><summary>高级参数（${advanced.length}）</summary>${advanced.map(([k,p])=>field(n,k,p,s)).join('')}</details>`:''}
  ${n.type==='repair_llm'?'<div class="wd-hint">无法安全修复时暂停并通知。只从失败节点重新计算，不承诺恢复到中断百分比。</div>':''}
  ${n.type==='interpret_llm'?'<div class="wd-hint">输出需要保留数据依据与限制；不得将 DNA 功能潜力解释为基因表达或临床诊断。</div>':''}
  ${!['start','end','analysis_group','fastq_input','repair_llm','interpret_llm'].includes(n.type)?`<details class="wd-custom" ${n.custom.length?'open':''}><summary>自定义参数（${n.custom.length}）</summary><p>仅保留设计，待后端映射；不接受密钥或任意脚本。</p>${n.custom.map((p,i)=>`<div class="wd-custom-row" data-custom-index="${i}"><label>参数名称<input data-custom-field="name" value="${esc(p.name)}" maxlength="64"></label><label>类型<select data-custom-field="type">${['string','integer','number','boolean'].map(t=>`<option ${p.type===t?'selected':''}>${t}</option>`).join('')}</select></label><label>值${p.type==='boolean'?`<select data-custom-field="value"><option value="true" ${p.value===true?'selected':''}>true</option><option value="false" ${p.value===false?'selected':''}>false</option></select>`:`<input data-custom-field="value" value="${esc(p.value)}" maxlength="12000">`}</label><label>参数作用<input data-custom-field="description" value="${esc(p.description)}" maxlength="600"></label>${btn('移除此参数','remove-custom',`data-index="${i}"`)}</div>`).join('')}${btn('＋ 添加自定义参数','add-custom')}</details>`:''}
  <details class="wd-ports-details"><summary>输入、输出与连接</summary><dl>${d.inputs.map(p=>`<dt>输入 ${esc(p.id)}</dt><dd>${esc(p.accepted_data_types.join(' / '))}</dd>`).join('')}${d.outputs.map(p=>`<dt>输出 ${esc(p.id)}</dt><dd>${esc(p.data_type)}</dd>`).join('')}</dl>${s.graph.edges.filter(e=>e.source===n.id||e.target===n.id).map(e=>`<div class="wd-edge-row"><span>${esc(s.graph.nodes.find(x=>x.id===e.source)?.label)} → ${esc(s.graph.nodes.find(x=>x.id===e.target)?.label)}</span>${btn('断开','delete-edge',`data-edge-id="${e.id}"`)}</div>`).join('')}
  </details><div class="wd-inspector-actions">${!['start','analysis_group','end','repair_llm'].includes(n.type)?btn('复制节点','duplicate'):''}${!['start','analysis_group','end'].includes(n.type)?btn('删除节点','delete','class="wd-danger"'):''}</div></div>`;
 }
 function draw(){if(state.page!=='workflows'||!$('.wd-app'))return;const s=get(),vp=$('.wd-viewport'),panel=$('.wd-inspector');if(vp)s.scroll=[vp.scrollLeft,vp.scrollTop];const top=panel?.scrollTop||0;$('.wd-app').outerHTML=html(s);const v=$('.wd-viewport');if(v){v.scrollLeft=s.scroll[0];v.scrollTop=s.scroll[1];}if($('.wd-inspector'))$('.wd-inspector').scrollTop=top;}
 function openDialog(title,body,callback=null){dialogAction=callback;dialogFocus=document.activeElement;const d=$('.wd-dialog');d.innerHTML=`<header><h2>${esc(title)}</h2>${btn('×','close-dialog','aria-label="关闭提示"')}</header><div>${body}</div><footer>${btn(callback?'取消':'关闭','close-dialog')}${callback?btn('确认继续','confirm','class="primary"'):''}</footer>`;d.showModal();d.querySelector('button').focus();}
 function closeDialog(){const d=$('.wd-dialog');d?.close();dialogAction=null;if(dialogFocus?.isConnected)dialogFocus.focus();}
 function installGraph(graph,publication=null){const s=get();checkpoint();s.graph=graph;s.edge=null;s.publication=publication;try{if(publication)localStorage.setItem(key()+'.publication',JSON.stringify({template_id:publication.template_id,version:publication.version}));else localStorage.removeItem(key()+'.publication');}catch{}s.view='editor';s.selected=graph.nodes.find(n=>M.roots.includes(n.type))?.id;s.panel='canvas';s.scroll=[0,0];s.pending=null;save();draw();}
 function replaceGraph(graph,publication=null){if(get().graph)openDialog('替换当前设计？','<p>将以新模板替换当前画布。旧设计可通过撤销恢复；也可以先取消并导出备份。</p>',()=>installGraph(graph,publication));else installGraph(graph,publication);}
 function pick(nid){const s=get();s.selected=nid;s.edge=null;s.panel='config';draw();}
 function tryConnect(source,port,target,targetPort){const s=get(),r=M.checkConnection(s.graph,source,port,target,targetPort,s.catalog);s.pending=null;if(!r.ok){status(r.message);return;}
  const apply=()=>mutate(g=>M.connect(g,source,port,target,targetPort,true,s.catalog));if(r.warning)openDialog('确认分析风险',`<p>${esc(r.warning.message)}</p><p>此确认仅记录在设计中，不会启动分析。</p>`,apply);else apply();
 }
 function validate(){const s=get(),r=M.validate(s.graph,s.catalog);openDialog('流程检查与输出预览',`<p class="wd-hint">仅检查设计结构，不启动计算。尚未执行样本、数据库、磁盘或模型可用性预检。</p><h3>${r.errors.length?'需要调整 '+r.errors.length+' 项':'结构检查通过'}</h3><ul>${r.errors.map(e=>`<li>${esc(e.message)}</li>`).join('')}</ul><h3>注意事项（${r.warnings.length}）</h3><ul>${r.warnings.map(e=>`<li>${esc(e.message)}</li>`).join('')}</ul><h3>计划输出</h3><p>各节点结果表与溯源 → 数值图表 → 有依据的初步解读。</p><p>本轮未产生新结果、未调用 LLM。JSON 设计文件不等于可执行 Nextflow 流程。</p>`);}
 function focusNode(n){const vp=$('.wd-viewport');if(vp){vp.scrollLeft=Math.max(0,n.x*get().zoom-vp.clientWidth/2+110*get().zoom);vp.scrollTop=Math.max(0,n.y*get().zoom-vp.clientHeight/2+70*get().zoom);}}
 async function command(action,el){const s=get(),n=selected();
  if(action==='back'){save();await navigate('analysis');return;}
  if(action==='templates'){s.view='templates';save();draw();return;}
  if(action==='resume'){s.view='editor';s.selected=s.graph.nodes.find(n=>n.type==='fastq_input')?.id;draw();return;}
  if(action==='save'){save();return;}
  if(action==='publish'){
   const checked=M.validate(s.graph,s.catalog);if(checked.errors.length)throw Error(checked.errors.map(x=>x.message).join('；'));
   if(!confirm('发布会创建固定版本供分析工作台使用，不启动计算。尚未接入的 16S、自定义参数会被拒绝；图表和解读仅作结果视图。继续？'))return;
   const k=key(),body={design:JSON.parse(M.exportJSON(s.graph,{portable:false}))};
   if(s.publication){body.template_id=s.publication.template_id;body.base_version=s.publication.version;}
   const record=await api(pa('/analysis/workflows/publish'),'POST',body);if(k!==key())return;
   s.publication=record;localStorage.setItem(key()+'.publication',JSON.stringify({template_id:record.template_id,version:record.version}));s.remote=s.remote.filter(r=>r.template_id!==record.template_id);s.remote.push({...record.record,visual:record.design,publication:record});status('已发布 v'+record.version+'，分析工作台可载入；历史任务不变。');return;
  }
  if(action==='import'){$('[data-wd-file]').click();return;}
  if(action==='export'){const blob=new Blob([M.exportJSON(s.graph)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=(s.graph.name||'workflow').replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').slice(0,80)+'.workflow.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);status('已导出设计；不包含文件、密钥及项目数据绑定。');return;}
  if(action==='validate'){validate();return;}
  if(action==='close-dialog'){closeDialog();return;}
  if(action==='confirm'){const fn=dialogAction;closeDialog();fn?.();return;}
  if(action==='clear-selection'){s.selected=null;s.edge=null;s.panel='canvas';draw();return;}
  if(action==='undo'||action==='redo'){const from=s[action],to=s[action==='undo'?'redo':'undo'];if(!from.length)return;to.push(JSON.stringify(s.graph));s.graph=JSON.parse(from.pop());s.pending=null;if(!s.graph.nodes.some(x=>x.id===s.selected))s.selected=null;save();draw();return;}
  if(action==='arrange'){mutate(g=>M.arrange(g,s.catalog));return;}
  if(action==='zoom-in'||action==='zoom-out'){s.zoom=Math.min(1.4,Math.max(.3,s.zoom+(action==='zoom-in'?.1:-.1)));draw();return;}
  if(action==='fit'){const vp=$('.wd-viewport'),d=dimensions(s.graph);s.zoom=Math.min(1,Math.max(.3,Math.min((vp.clientWidth-30)/d.width,(vp.clientHeight-30)/d.height)));s.scroll=[0,0];if(vp){vp.scrollLeft=0;vp.scrollTop=0;}draw();return;}
  if(action==='delete'&&n){openDialog('删除这个节点？',`<p>删除 ${esc(n.label)} 及其连接；可以撤销恢复。</p>`,()=>mutate(g=>{M.removeNode(g,n.id);s.selected=null;}));return;}
  if(action==='duplicate'&&n){mutate(g=>{const copy=M.addNode(g,n.type,s.catalog);copy.label=n.label+' 副本';copy.parameters=JSON.parse(JSON.stringify(n.parameters));copy.custom=JSON.parse(JSON.stringify(n.custom));copy.x=n.x+35;copy.y=n.y+190;s.selected=copy.id;});return;}
  if(action==='add-custom'&&n){mutate(()=>M.addCustom(n));return;}
  if(action==='remove-custom'&&n){mutate(()=>n.custom.splice(Number(el.dataset.index),1));return;}
  if(action==='delete-edge'){const eid=el?.dataset?.edgeId||s.edge;if(!s.graph.edges.some(e=>e.id===eid))return;mutate(g=>{g.edges=g.edges.filter(e=>e.id!==eid);s.edge=null;});status('已删除连线，节点保留；可撤销恢复。');return;}
 }
 root.addEventListener('click',async e=>{
  if(state.page!=='workflows'||!bioflowUser)return;const el=e.target.closest('button,[data-wd-edge]');if(!el)return;
  try{
   if(el.dataset.wdTemplate){replaceGraph(M.template(el.dataset.wdTemplate,get().catalog));return;}
   if(el.dataset.wdServer){const record=get().remote.find(r=>r.template_id===el.dataset.wdServer);if(record){replaceGraph(record.visual?M.importJSON(JSON.stringify(record.visual),{portable:true,c:get().catalog}):M.fromWorkflow(record,get().catalog),record.visual?record.publication:null);return;}}
   if(el.dataset.wdAction){await command(el.dataset.wdAction,el);return;}
   if(el.dataset.wdPanel){get().panel=el.dataset.wdPanel;draw();return;}
   if(el.dataset.wdAdd){mutate(g=>{const n=M.addNode(g,el.dataset.wdAdd,get().catalog);const vp=$('.wd-viewport');n.x=Math.max(350,Math.round((vp?.scrollLeft||0)/get().zoom+100));n.y=Math.max(180,Math.round((vp?.scrollTop||0)/get().zoom+170));get().selected=n.id;get().panel='config';});return;}
   if(el.dataset.wdSelect){if(drag?.moved)return;pick(el.dataset.wdSelect);return;}
   if(el.dataset.wdOut){get().pending={node:el.dataset.node,port:el.dataset.wdOut};status('已选择输出 '+el.dataset.wdOut+'，请点击目标输入端口（Esc 取消）。');return;}
   if(el.dataset.wdIn){const p=get().pending;if(!p){status('请先选择一个输出端口，再选择输入端口。');return;}tryConnect(p.node,p.port,el.dataset.node,el.dataset.wdIn);return;}
   if(el.dataset.wdEdge){const s=get();s.edge=el.dataset.wdEdge;s.selected=null;s.panel='config';draw();return;}
   if(el.dataset.wdServer){const k=key(),s=get(),r=await service('analysis','/api/workflows/templates/'+id(el.dataset.wdServer));if(k!==key()||state.page!=='workflows')return;replaceGraph(M.fromWorkflow(r,s.catalog));}
  }catch(error){status(error.message);}
 });
 function updateValue(el){const s=get(),n=selected();if(!s.graph)return;
  if(el.dataset.wdInlineModel){const target=s.graph.nodes.find(x=>x.id===el.dataset.wdInlineModel);if(!target)return;checkpoint();target.parameters.model_id=el.value;save();syncModel(target,s);return;}
  if(el.matches('[data-wd-name]')){checkpoint();s.graph.name=el.value;save();return;}
  if(!n)return;
  if(el.matches('[data-wd-label]')){checkpoint();n.label=el.value||s.catalog[n.type].label;save();return;}
  const k=el.dataset.wdParam;if(k){const schema=s.catalog[n.type].parameters[k];checkpoint();n.parameters[k]=M.parameterValue(schema.type,schema.type==='boolean'?el.checked:el.value);save();if(k==='model_id')syncModel(n,s);return;}
  if(el.dataset.customField){const row=el.closest('[data-custom-index]'),p=n.custom[Number(row.dataset.customIndex)];checkpoint();const f=el.dataset.customField;if(f==='value')p.value=M.parameterValue(p.type,el.value);else p[f]=el.value;if(f==='type')p.value=p.type==='boolean'?false:['number','integer'].includes(p.type)?0:'';save();if(f==='type')draw();}
 }
 root.addEventListener('input',e=>{if(state.page==='workflows'&&bioflowUser&&e.target.matches('input:not([type=checkbox]):not([type=file]),textarea'))updateValue(e.target);});
 root.addEventListener('change',async e=>{
  if(state.page!=='workflows'||!bioflowUser)return;
  if(e.target.matches('select,input[type=checkbox]'))updateValue(e.target);
  if(e.target.matches('[data-wd-file]')){const file=e.target.files[0],k=key();e.target.value='';if(!file)return;try{if(file.size>2000000)throw Error('模板大小超过 2 MB 限制');const text=await file.text();if(k!==key()||state.page!=='workflows')return;replaceGraph(M.importJSON(text,{c:get().catalog}));}catch(error){status(error.message);}}
 });
 root.addEventListener('pointerdown',e=>{
  if(state.page!=='workflows'||!bioflowUser||e.button!==0)return;const s=get(),title=e.target.closest('.wd-node-title'),out=e.target.closest('[data-wd-out]'),vp=e.target.closest('.wd-viewport');
  if(out){drag={kind:'wire',start:[e.clientX,e.clientY],node:out.dataset.node,port:out.dataset.wdOut,moved:false,key:key()};return;}
  if(title){const n=s.graph.nodes.find(n=>n.id===title.dataset.wdSelect);drag={kind:'node',n,start:[e.clientX,e.clientY],origin:[n.x,n.y],moved:false,key:key()};return;}
  if(vp&&!e.target.closest('button,.wd-node,[data-wd-edge]'))drag={kind:'pan',vp,start:[e.clientX,e.clientY],origin:[vp.scrollLeft,vp.scrollTop],moved:false,key:key()};
 });
 window.addEventListener('pointermove',e=>{
  if(!drag||drag.key!==key()||state.page!=='workflows')return;const dx=e.clientX-drag.start[0],dy=e.clientY-drag.start[1];if(Math.abs(dx)+Math.abs(dy)<6&&!drag.moved)return;
  if(!drag.moved&&drag.kind==='node')checkpoint();drag.moved=true;
  if(drag.kind==='pan'){drag.vp.scrollLeft=drag.origin[0]-dx;drag.vp.scrollTop=drag.origin[1]-dy;return;}
  if(drag.kind==='wire'){status('将连接拖到目标节点的输入端口。');return;}
  const n=drag.n;n.x=Math.max(n.group?330:0,Math.min(30000,Math.round(drag.origin[0]+dx/get().zoom)));n.y=Math.max(n.group?110:0,Math.min(30000,Math.round(drag.origin[1]+dy/get().zoom)));
  const card=$(`.wd-node[data-node="${n.id}"]`);if(card){card.style.left=n.x+'px';card.style.top=n.y+'px';}const svg=$('.wd-edges');if(svg)svg.innerHTML=edges(get());const group=$('.wd-analysis-group');if(group){const b=M.groupBounds(get().graph);group.style.width=b.width+'px';group.style.height=b.height+'px';}
 });
 window.addEventListener('pointerup',e=>{if(!drag)return;const d=drag;drag=null;if(d.key!==key()||state.page!=='workflows')return;if(d.kind==='wire'&&d.moved){const target=document.elementFromPoint(e.clientX,e.clientY)?.closest('[data-wd-in]');if(target)tryConnect(d.node,d.port,target.dataset.node,target.dataset.wdIn);else status('连接未建立：请拖到输入端口。');return;}if(d.kind==='node'&&d.moved){save();draw();}});
 root.addEventListener('keydown',e=>{if(state.page!=='workflows')return;if(e.key==='Escape'){get().pending=null;status('已取消连接。');return;}const editing=e.target.closest('input,select,textarea,[contenteditable="true"]');if(editing||$('.wd-dialog')?.open)return;
  if(['Delete','Backspace'].includes(e.key)&&get().edge){e.preventDefault();command('delete-edge');return;}
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();command(e.shiftKey?'redo':'undo');}
  if(e.altKey&&['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)){const n=selected();if(!n||n.type==='analysis_group')return;e.preventDefault();mutate(()=>{n.x=Math.max(n.group?330:0,n.x+({ArrowLeft:-20,ArrowRight:20}[e.key]||0));n.y=Math.max(n.group?110:0,n.y+({ArrowUp:-20,ArrowDown:20}[e.key]||0));});$(`[data-wd-select="${n.id}"]`)?.focus();}
 });
 window.addEventListener('pointercancel',()=>{if(drag?.kind==='node'&&drag.moved)save();drag=null;});
 root.addEventListener('cancel',e=>{if(e.target.matches('.wd-dialog')){e.preventDefault();closeDialog();}},true);
})();
