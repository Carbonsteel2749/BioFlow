'use strict';
/* Analysis-only preview: existing authenticated APIs are read, not resubmitted. */
const wbModel=window.BioFlowAnalysis;
const wbStates=new Map();
let wbRequest=0,wbLogRequest=0;
const wbKey=()=>`${bioflowUser?.id||'guest'}:${state.project}`;
function wbState(){
 const key=wbKey();
 if(!wbStates.has(key)){
  let saved={};try{saved=JSON.parse(localStorage.getItem('bioflow.analysis-preview.'+key)||'{}');}catch{}
  wbStates.set(key,{mode:'configure',template:'standard',node:'data',tab:'parameters',draft:{name:current()?.name||'',dataset_id:'',parameters:{},...saved},task:null,artifacts:[],logs:[],interpretation:null,errors:[],config:null,datasets:[],limits:null,list:[],review:false});
 }
 return wbStates.get(key);
}
function wbPersist(w){
 try{localStorage.setItem('bioflow.analysis-preview.'+wbKey(),JSON.stringify({name:w.draft.name,dataset_id:w.draft.dataset_id,parameters:w.draft.parameters}));}catch{notify('浏览器无法保存草稿；本次页面内仍会保留，请勿关闭。');}
}
function wbCapture(){
 const w=wbState(),form=$('[data-form="wb-config"]');if(!form)return;
 for(const key of ['name','dataset_id']){const el=form.elements.namedItem(key);if(el)w.draft[key]=el.value;}
 form.querySelectorAll('[data-wb-param]').forEach(el=>{w.draft.parameters[el.name]=el.type==='checkbox'?el.checked:el.value;});
 wbPersist(w);w.review=false;
}
const wbChrome=chrome;
chrome=function(){root.classList.toggle('analysis-mode',state.page==='analysis');return wbChrome();};
const wbNavigate=navigate;
navigate=async function(page){if(state.page==='analysis')wbCapture();++wbRequest;return wbNavigate(page);};
const wbStatus=s=>wbModel.statusLabels[s]||s||'未取得状态';
const wbBadge=s=>`<span class="wb-badge wb-status-${esc(s||'unknown')}">${esc(wbStatus(s))}</span>`;
const wbIcon=(key)=>`<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="${({data:'M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h5',qc:'M9 3h6 M10 3v7l-5 9q0 2 2 2h10q2 0 2-2l-5-9V3 M8 15h8',host:'M12 3l8 3v6c0 5-8 9-8 9S4 17 4 12V6z M8 12l3 3 5-6',taxonomy:'M12 3v7 M5 16v-5h14v5 M2 17h6v4H2z M16 17h6v4h-6z M9 3h6v4H9z',functional:'M7 3c0 7 10 11 10 18 M17 3C17 10 7 14 7 21 M8 6h8 M9 10h6 M9 14h6 M8 18h8',pathways:'M4 6h6v6H4z M14 13h6v6h-6z M10 9h7v4 M7 12v5h7',report:'M5 3h10l4 4v14H5z M14 3v5h5 M8 12h8 M8 16h8',catalog:'M4 5h16v4H4z M4 11h16v4H4z M4 17h16v4H4z'})[key]||'M4 4h16v16H4z'}"/></svg>`;
function wbHeader(w){
 return `<div class="wb-heading"><div><div class="eyebrow">BIOFLOW / ANALYSIS STUDIO</div><h1>数据分析工作台</h1><p>从样本到结果，让每一步都清晰可见。</p></div><span class="wb-preview-label">分支预览 · 不提交计算</span></div>
 <label class="wb-project-switch">当前项目<select data-wb-project>${state.projects.map(p=>`<option value="${esc(p.id)}" ${p.id===state.project?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label>
 <div class="wb-toolbar"><div class="wb-view-tabs" aria-label="分析视图">${button('新建分析','wb-mode','data-mode="configure" class="'+(w.mode==='configure'?'selected':'')+'"')}${button(`项目任务 <span>${w.list.length}</span>`,'wb-mode','data-mode="tasks" class="'+(w.mode!=='configure'?'selected':'')+'"')}</div><div class="row">${button('设计 Workflow','workflows')}${button('刷新','wb-refresh')}</div></div>`;
}
function wbTaskList(w){
 return `<section class="wb-history" aria-label="项目分析任务"><div class="wb-section-head"><div><h2>项目任务</h2><p>打开已有任务，按节点查看参数、日志和结果。</p></div>${button('关联已有任务','import-tasks')}</div><div class="wb-task-list">${w.list.map(t=>`<article class="wb-task-card"><span class="wb-task-icon">${wbIcon('report')}</span><div class="wb-task-copy"><h3>${esc(t.name||'未命名任务')}</h3><p><span>${esc(t.id.slice(0,8))}</span> · ${esc(t.created_at?new Date(t.created_at).toLocaleDateString('zh-CN'):'历史任务')}</p></div>${wbBadge(t.status)}<div class="wb-task-actions">${button('进度与结果','wb-task',`data-id="${esc(t.id)}" class="wb-open-task"`)}${button('绘图 ↗','task-charts',`data-id="${esc(t.id)}"`)}</div></article>`).join('')||'<div class="wb-empty">当前项目还没有任务。先配置分析，或关联已有任务。</div>'}</div></section>`;
}
function wbRail(w){
 const template=wbModel.templates[w.mode==='run'?'standard':w.template];
 return `<nav class="wb-rail" aria-label="分析流程节点"><div class="wb-rail-heading"><span>分析流程</span><small>${template.nodes.length} 个展示节点</small></div>${template.nodes.map((n,i)=>{
  const status=w.mode==='run'?wbModel.nodeStatus(w.task,n.id):null;
  return `<button type="button" class="wb-node ${w.node===n.id?'selected':''}" data-action="wb-node" data-node="${n.id}" aria-current="${w.node===n.id?'step':'false'}"><span class="wb-node-icon">${status==='succeeded'?'✓':wbIcon(n.id)}</span><span class="wb-node-copy"><strong>${esc(n.label)}</strong><small>${status?esc(wbStatus(status)):esc(n.tool)}</small></span><span class="wb-node-number">${String(i+1).padStart(2,'0')}</span></button>`;
 }).join('')}<p class="wb-rail-note">物种与功能是去宿主后的两条分支。通路属于功能结果视图，不重复计算。</p></nav>`;
}
function wbSchema(w,key){
 const s=w.config?.parameters_schema?.[key],limit=w.limits?.cpu?.max_threads;
 return key==='threads'&&s&&Number.isFinite(limit)?{...s,maximum:Math.min(s.maximum??limit,limit)}:s;
}
function wbField(w,key){
 const s=wbSchema(w,key);if(!s)return '';
 const value=w.draft.parameters[key]??s.default??'';
 const label=analysisLabels[key]||s.title||key;
 const attrs=`name="${esc(key)}" data-wb-param="${esc(key)}"`;
 let control;
 if(s.type==='boolean')return `<label class="wb-toggle"><input ${attrs} type="checkbox" ${value===true?'checked':''}><span><b>${esc(label)}</b><small>${esc(s.description||key)}</small></span></label>`;
 if(s.enum)control=`<select ${attrs}>${s.enum.map(v=>`<option value="${esc(v)}" ${String(value)===String(v)?'selected':''}>${esc(analysisEnumLabels[v]||v)}</option>`).join('')}</select>`;
 else control=`<input ${attrs} type="${['integer','number'].includes(s.type)?'number':'text'}" value="${esc(value)}" ${s.minimum!==undefined?`min="${s.minimum}"`:''} ${s.maximum!==undefined?`max="${s.maximum}"`:''} step="${s.type==='integer'?'1':'any'}">`;
 return `<label class="wb-field"><span>${esc(label)}${s.unit?` <small>${esc(s.unit)}</small>`:''}</span>${control}<small>${esc(s.description||'采用当前服务登记的参数定义。')}</small><span class="wb-field-meta">${esc(key)} · 默认 ${esc(s.default??'未指定')}${s.maximum!==undefined?' · 上限 '+esc(s.maximum):''}</span></label>`;
}
function wbRefs(w,keys){
 const refs=w.config?.references?.items||[];
 return refs.filter(r=>keys.includes(r.key)).map(r=>`<div class="wb-reference"><div><b>${esc(r.database_name)}</b><span class="wb-muted">${esc(r.release||'版本未登记')}</span></div><p>${esc(r.tool_compatibility_version||'兼容版本未登记')}</p><details><summary>数据库位置与登记信息</summary><code>${esc(r.path)}</code><p>${esc(r.ready===true?'关键文件检查通过':r.ready===false?'关键文件检查未通过':'登记已读取；运行前仍需校验关键文件')}</p></details></div>`).join('')||'<p class="wb-muted">当前服务未返回此项数据库登记。</p>';
}
function wbConfigurePanel(w,n){
 if(w.template==='paper')return `<div class="wb-callout warning"><b>论文方法设计稿 · 尚不可运行</b><p>以下内容来自 Zeller 2014 的方法部分（第 11–13 页）。历史软件、数据库和执行适配尚未接入，不会静默切换为现代方法。</p></div><h3>原文方法</h3><p>${esc(n.description)}</p><dl class="wb-definitions"><dt>工具 / 方法</dt><dd>${esc(n.tool)}</dd><dt>预期输出</dt><dd>${esc(n.output)}</dd><dt>依赖状态</dt><dd>待核对原文版本与数据库可获得性</dd></dl><div class="wb-callout"><b>复现边界</b><p>六样本每个仅 10 万对 reads，适合工程验收；不能据此复现组间结论或分类模型。物种定量与基因目录是独立分支。</p></div>`;
 if(n.id==='data'){
  const d=w.datasets.find(d=>d.id===w.draft.dataset_id);
  return `<div class="wb-config-grid"><label class="wb-field"><span>任务名称</span><input name="name" maxlength="120" value="${esc(w.draft.name)}" placeholder="为这次分析起个名字"><small>任务、结果和图表将使用同一名称关联。</small></label><label class="wb-field"><span>项目数据集</span><select name="dataset_id"><option value="">请选择已配对的数据集</option>${w.datasets.map(d=>`<option value="${esc(d.id)}" ${d.id===w.draft.dataset_id?'selected':''} ${!['available','not_checked'].includes(d.status)?'disabled':''}>${esc(d.name)} · ${d.sample_count??'?'} 个样本</option>`).join('')}</select><small>只列出当前项目已关联的数据，不需要重新上传。</small></label></div><div class="wb-data-summary"><span class="wb-large-icon">${wbIcon('data')}</span><div><b>${d?esc(d.name):'从一个数据集开始'}</b><p>${d?`${d.sample_count??'未知'} 个样本 · ${esc(d.status==='available'?'数据可用':'文件待检查')}`:'选择 FASTQ 数据集后，继续配置各个分析节点。'}</p></div></div><div class="row">${button('查看样本与分组','wb-groups',w.draft.dataset_id?'':'disabled')}${button('上传与文件配对 ↗','upload-manager')}</div><div class="wb-callout"><b>六样本测试提示</b><p>PRJEB6070 的轻量子集用于验证流程与多样本图表。分组不平衡，不用于疾病差异或临床结论。</p></div>`;
 }
 if(n.id==='qc')return `<div class="wb-method-card"><span>01 / 质量评估</span><b>FastQC</b><p>检查质量分布、接头与序列组成；检查报告本身不清洗 reads。</p></div><h3>02 / fastp 清洗参数</h3><div class="wb-config-grid">${analysisCommon.filter(k=>k!=='threads').map(k=>wbField(w,k)).join('')}</div><details class="wb-advanced"><summary>高级清洗参数 <span>${analysisAdvanced.length} 项</span></summary><div class="wb-config-grid">${analysisAdvanced.map(k=>wbField(w,k)).join('')}</div></details>`;
 if(n.id==='host')return `<div class="wb-reference"><b>已登记宿主索引 · ${esc(w.config.references.host.name)}</b><details><summary>查看索引位置</summary><code>${esc(w.config.references.host.path||'未配置')}</code></details></div><div class="wb-config-grid">${analysisHost.map(k=>wbField(w,k)).join('')}</div><div class="wb-callout"><b>过滤含义</b><p>${esc(hostEffect(w.draft.parameters.host_filter_mode||w.config.parameters_schema.host_filter_mode?.default))}</p></div>`;
 if(n.id==='taxonomy')return `${wbRefs(w,['kraken2','bracken'])}<div class="wb-config-grid">${wbField(w,'read_length')}</div><div class="wb-callout"><b>结果边界</b><p>这是基于 Kraken2 / Bracken 的 reads 层面组成估计，不是论文中的单拷贝标记基因定量。物种结果不作为总体功能的归属证据。</p></div>`;
 if(n.id==='functional')return `${wbRefs(w,['humann_nucleotide','humann_protein','metaphlan'])}<details class="wb-advanced"><summary>KO / EC 配套映射</summary>${wbRefs(w,['ko_mapping','ec_mapping'])}</details><div class="wb-callout"><b>同一输入，独立解释</b><p>输入为去宿主 reads。基因家族、KO 和 EC 描述群落功能潜力，不代表实际表达或代谢活性。</p></div>`;
 if(n.id==='pathways')return `<div class="wb-method-card"><span>同一计算的不同结果</span><b>HUMAnN / MetaCyc</b><p>通路丰度与覆盖度由功能注释产生，这个节点用于查看与解释，不重复运行 HUMAnN。</p></div><div class="wb-output-preview"><article><b>通路丰度</b><p>保留来源单位及归一化信息，不按数值大小猜测百分比。</p></article><article><b>通路覆盖度</b><p>与丰度分开展示，不相加，也不等同于通路表达。</p></article></div>`;
 return `<div class="wb-output-preview"><article><b>结果文件</b><p>按阶段和样本组织表格、报告及运行溯源。</p></article><article><b>科研图表</b><p>从真实来源表进入图表工作台，保留版本及参数。</p></article><article><b>初步解读</b><p>显示数据依据与局限，不自动宣称显著差异。</p></article><article><b>任务归档</b><p>使用现有归档机制；本次不改动结果包。</p></article></div><div class="wb-callout">MAG 是可选扩展，不是这条标准 reads 流程的必要步骤。当前 MAG 数据库未就绪，不在这版预览中启用。</div>`;
}
function wbRuntime(w){
 const cpu=w.limits?.cpu;
 return `<details class="wb-runtime"><summary>运行设置 <span>${esc(w.draft.parameters.threads??w.config?.parameters_schema.threads?.default??'—')} 线程 · AI 默认关闭</span></summary><div class="wb-config-grid">${wbField(w,'threads')}<div class="wb-resource"><b>当前服务资源</b><p>线程上限 <strong>${cpu?.max_threads??'未知'}</strong> · 建议剩余 <strong>${cpu?.suggested_remaining_threads??'未知'}</strong></p><p>运行 ${w.limits?.running?.length??'—'} / 排队 ${w.limits?.queued?.length??'—'} · 并发上限 ${w.limits?.limit??'—'}</p><small>仅是本分析服务的配置估算，不代表系统实时空闲 CPU；空间估算将在正式提交前检查。</small></div></div><label class="wb-toggle"><input type="checkbox" disabled><span><b>受限 AI 自动修复</b><small>${esc(w.config?.capabilities.auto_repair_available?'服务支持；本轮为只读预览，暂不提交修复设置。':w.config?.capabilities.auto_repair_unavailable_reason||'服务能力未知')}</small></span></label></details>`;
}
function wbWorkspace(w){
 const run=w.mode==='run', template=wbModel.templates[run?'standard':w.template];
 const n=template.nodes.find(n=>n.id===w.node)||template.nodes[0];w.node=n.id;
 const tabs=run?[['results','结果文件'],['parameters','运行参数'],['logs','阶段日志'],['interpretation','初步解读']]:[['parameters',w.template==='paper'?'方法与依赖':'参数配置'],['outputs','输入与输出']];
 if(!tabs.some(t=>t[0]===w.tab))w.tab=tabs[0][0];
 return `${run?`<div class="wb-run-heading"><div>${button('← 返回项目任务','wb-mode','data-mode="tasks"')}<h2>${esc(w.task.name||w.task.id)}</h2><p>${esc(w.task.id)}</p></div>${wbBadge(w.task.status)}</div>`:`<div class="wb-template-bar"><label>分析模板<select data-wb-template>${Object.entries(wbModel.templates).map(([key,t])=>`<option value="${key}" ${key===w.template?'selected':''}>${esc(t.name)}${t.executable?'':'（设计稿）'}</option>`).join('')}</select></label><span class="wb-template-tag">${esc(template.version)}</span><p>${w.template==='paper'?'原文方法逐步对齐，依赖未齐不允许启动。':'成熟流程 · 原始数据 → 物种与功能 → 结果汇总'}</p></div>`}
 <${run?'div':'form'} ${run?'':'data-form="wb-config"'} class="wb-workspace">${wbRail(w)}<div class="wb-main-panel"><div class="wb-node-heading"><span class="wb-panel-icon">${wbIcon(n.id)}</span><div><div class="wb-overline">${run?'运行结果':'节点配置'} / ${esc(n.tool)}</div><h2>${esc(n.label)}</h2></div>${run?wbBadge(wbModel.nodeStatus(w.task,n.id)):'<span class="wb-draft-tag">草稿</span>'}</div><p class="wb-node-description">${esc(n.description)}</p><div class="wb-panel-tabs" role="tablist" aria-label="节点详情">${tabs.map(([key,label])=>`<button type="button" role="tab" aria-selected="${w.tab===key}" data-action="wb-tab" data-tab="${key}">${label}</button>`).join('')}</div><section class="wb-panel-body" role="tabpanel">${run?wbRunPanel(w,n):w.tab==='outputs'?`<dl class="wb-definitions"><dt>输入</dt><dd>${esc(n.id==='data'?'当前项目的双端 FASTQ 与样本信息':n.id==='taxonomy'||n.id==='functional'?'同一批去宿主 reads，彼此无先后依赖':'上游节点的已验证产物')}</dd><dt>输出</dt><dd>${esc(n.output)}</dd><dt>关联方式</dt><dd>任务 ID、节点 ID、样本 ID 与产物 ID 共同定位。</dd></dl>`:wbConfigurePanel(w,n)}</section>${run?'':wbRuntime(w)}${run?'':`<div class="wb-bottom-actions"><span>草稿保存在此浏览器 · 未提交任务</span><div class="row">${button('保存草稿','wb-save')}${button('预览运行配置','wb-review','class="primary" '+(template.executable?'':'disabled title="论文方法尚未接入"'))}</div></div>`}</div></${run?'div':'form'}>`;
}
function wbRunPanel(w,n){
 if(w.tab==='parameters'){
  const p=w.task.parameters||{};
  const keys=Object.keys(p).filter(k=>k==='threads'||(['taxonomy','functional','pathways'].includes(n.id)&&['figure_ai_advice','figure_extended_plots'].includes(k))||({qc:k.startsWith('fastp_'),host:k.startsWith('host_'),taxonomy:['kraken_db','read_length'].includes(k)||k.startsWith('taxonomy_'),functional:/humann|metaphlan|functional_plot/.test(k),pathways:/humann|functional_plot/.test(k),data:false,report:true})[n.id]);
  return `<p class="wb-muted">这是该次运行的只读参数快照，修改新任务不会改写历史。</p><dl class="wb-definitions">${keys.map(k=>`<dt>${esc(analysisLabels[k]||k)}</dt><dd>${esc(typeof p[k]==='object'?JSON.stringify(p[k]):p[k])}</dd>`).join('')||'<dd>该节点未登记额外参数。</dd>'}</dl>`;
 }
 if(w.tab==='logs'){
  const files=w.logs.filter(f=>n.id==='report'||(wbModel.stages[n.id]||[]).includes(f.step));
  return `<p class="wb-muted">按真实阶段归类；预览不会删除日志。</p><div class="wb-file-list">${files.map(f=>`<article><div><b>${esc(f.name)}</b><small>${esc(f.step)} · ${uploadBytes(f.size_bytes||0)}</small></div>${button('预览','wb-log',`data-file="${esc(f.id)}"`)}</article>`).join('')||'<div class="wb-empty">该节点没有可显示的已登记日志。</div>'}</div>`;
 }
 if(w.tab==='interpretation'){
  const cats={qc:['qc'],host:['qc'],taxonomy:['taxonomy'],functional:['functional'],pathways:['functional']};
  const sections=(w.interpretation?.display_sections||w.interpretation?.sections||[]).filter(s=>!cats[n.id]||cats[n.id].includes(s.category));
  const warning=Array.isArray(w.interpretation?.warnings)?w.interpretation.warnings:[],limits=Array.isArray(w.interpretation?.limitations)?w.interpretation.limitations:[];
  return `<div class="wb-callout">${w.interpretation?.partial?'阶段性解读：只采用已确认完成阶段的数据。':'已有结果的描述性解读，不重新调用模型。'}宏基因组功能是功能潜力，不等同于表达、代谢活性或临床结论。</div>${warning.map(text=>`<div class="wb-callout warning">${esc(text)}</div>`).join('')}${sections.map(s=>`<article class="wb-interpretation"><h3>${esc(s.title||s.sample_id||'分析摘要')}</h3><p>${esc(s.text||s.summary||'请通过任务原始解读查看完整内容。')}</p></article>`).join('')||'<div class="wb-empty">该阶段没有可用解读。</div>'}${limits.length?`<div class="wb-callout"><b>分析限制</b>${limits.map(text=>`<p>${esc(text)}</p>`).join('')}</div>`:''}`;
 }
 const artifacts=wbModel.nodeArtifacts(w.artifacts,n.id,w.task.id);
 const images=artifacts.filter(a=>a.media_type==='image/png'&&a.downloadable).slice(0,2);
 return `<div class="wb-result-summary"><span><b>${artifacts.length}</b> 项已登记产物</span>${button('进入图表工作台 ↗','task-charts',`data-id="${esc(w.task.id)}"`)}</div>${w.task.error_message?`<div class="wb-callout warning">${esc(w.task.error_message)}</div>`:''}${w.errors.map(e=>`<div class="wb-callout warning">${esc(e)}</div>`).join('')}<div class="wb-figure-grid">${images.map(a=>`<figure><a href="/api/projects/${id(state.project)}/artifacts/${id(a.artifact_id)}/preview" target="_blank" rel="noopener"><img loading="lazy" src="/api/projects/${id(state.project)}/artifacts/${id(a.artifact_id)}/preview" alt="${esc(a.file_name)}"></a><figcaption>${esc(a.file_name)} · ${esc(a.sample_id||'多样本汇总')}</figcaption></figure>`).join('')}</div><div class="wb-file-list">${artifacts.slice(0,60).map(a=>`<article><div><b>${esc(a.file_name)}</b><small>${esc(a.sample_id||'任务汇总')} · ${uploadBytes(a.size_bytes||0)} · ${esc(a.status||'状态未登记')}</small></div>${a.downloadable?`<a class="wb-download" href="/services/analysis/api/artifacts/${id(a.artifact_id)}/download" target="_blank" rel="noopener">下载 ↗</a>`:'<span class="wb-muted">不可下载</span>'}</article>`).join('')||'<div class="wb-empty">该节点尚无已登记产物；不代表生物学结果为零。<br>原始阶段文件可能仍保留在任务归档中。</div>'}</div>${artifacts.length>60?'<p class="wb-muted">此处展示前 60 项；完整目录可在“资料”页查看。</p>':''}${n.id==='report'?`<a class="wb-archive" href="/api/projects/${id(state.project)}/tasks/${id(w.task.id)}/archive">下载该任务现有归档 ↗</a>`:''}`;
}
views.analysis=async function(){
 const key=wbKey(),pid=state.project,w=wbState(),sequence=++wbRequest;
 if(w.mode==='run'&&w.task)return wbHeader(w)+wbWorkspace(w);
 if(w.mode==='tasks'){
  const list=await tasks();if(sequence!==wbRequest||key!==wbKey())return '';w.list=list;
  return wbHeader(w)+wbTaskList(w);
 }
 const [config,data,list,limits]=await Promise.all([api(`/api/projects/${id(pid)}/analysis/options`),api(`/api/projects/${id(pid)}/analysis/datasets`),tasks(),service('analysis','/api/resources/limits').catch(()=>null)]);
 if(sequence!==wbRequest||key!==wbKey())return '';
 Object.assign(w,{config,datasets:data.items||[],list,limits});
 return wbHeader(w)+(w.mode==='tasks'?wbTaskList(w):wbWorkspace(w)+(w.mode==='configure'?wbTaskList(w):''));
};
actions['wb-mode']=async el=>{wbCapture();const w=wbState();w.mode=el.dataset.mode;w.tab='parameters';w.node='data';++wbRequest;await render();};
actions['wb-node']=async el=>{wbCapture();const w=wbState();w.node=el.dataset.node;await render();$(`[data-action="wb-node"][data-node="${w.node}"]`)?.focus({preventScroll:true});};
actions['wb-tab']=async el=>{wbCapture();wbState().tab=el.dataset.tab;await render();$(`[data-action="wb-tab"][data-tab="${wbState().tab}"]`)?.focus({preventScroll:true});};
actions['wb-save']=()=>{wbCapture();notify('分析草稿已保存在此浏览器；没有创建任务或修改服务器数据。');};
actions['wb-review']=()=>{
 wbCapture();const w=wbState();if(!wbModel.templates[w.template]?.executable){notify('论文方法设计稿尚不可运行，请先完成工具和数据库适配。');return;}const d=w.datasets.find(d=>d.id===w.draft.dataset_id);
 if(!w.draft.name.trim()||!d){w.node='data';render().then(()=>notify('请填写任务名称，并选择当前项目的数据集。'));return;}
 const bad=Object.entries(w.draft.parameters).find(([k,v])=>{const s=wbSchema(w,k);if(!s)return true;if(!['integer','number'].includes(s.type))return false;return v===''||!Number.isFinite(Number(v))||(s.type==='integer'&&!Number.isInteger(Number(v)))||(s.minimum!==undefined&&Number(v)<s.minimum)||(s.maximum!==undefined&&Number(v)>s.maximum);});
 if(bad){notify('参数 '+bad[0]+' 不符合当前服务登记的范围，请调整。');return;}
 modal('运行配置预览',`<div class="wb-callout warning">这是前端设计预览，仅核对已填写字段。尚未进行文件、数据库与磁盘预检，也不会启动计算。</div><dl class="wb-definitions"><dt>任务名称</dt><dd>${esc(w.draft.name)}</dd><dt>数据集</dt><dd>${esc(d.name)} · ${d.sample_count} 个样本</dd><dt>流程</dt><dd>标准宏基因组分析</dd><dt>AI 自动修复</dt><dd>关闭</dd></dl><details><summary>查看草稿中已修改的参数</summary>${pre(w.draft.parameters)}</details>`);
};
actions['wb-task']=async el=>{
 wbCapture();const key=wbKey(),w=wbState(),tid=el.dataset.id,sequence=++wbRequest;
 $('.content').innerHTML='<div class="wb-empty" role="status">正在读取任务节点和结果目录…</div>';
 const result=await Promise.allSettled([service('analysis',`/api/tasks/${id(tid)}`),service('analysis',`/api/tasks/${id(tid)}/artifacts`),service('analysis',`/api/tasks/${id(tid)}/log-files`),service('analysis',`/api/tasks/${id(tid)}/interpretation`)]);
 if(sequence!==wbRequest||key!==wbKey()||state.page!=='analysis')return;
 if(result[0].status==='rejected'||!result[0].value||result[0].value.id!==tid){w.mode='tasks';await render();notify('任务读取失败：'+(result[0].reason?.message||'任务信息返回格式异常'));return;}
 const errors=[];
 function optional(index,label,check,select,fallback){
  const response=result[index];
  if(response.status==='fulfilled'&&check(response.value))return select(response.value);
  errors.push(label+'暂不可用：'+(response.reason?.message||'返回格式异常'));return fallback;
 }
 const artifacts=optional(1,'结果目录',v=>Array.isArray(v)||Array.isArray(v?.items),v=>items(v).filter(a=>a&&typeof a.artifact_id==='string'&&typeof a.file_name==='string'),[]);
 const logs=optional(2,'日志目录',v=>Array.isArray(v?.files),v=>v.files.filter(f=>f&&typeof f.id==='string'&&typeof f.name==='string'),[]);
 const interpretation=optional(3,'初步解读',v=>v&&typeof v==='object'&&(Array.isArray(v.display_sections)||Array.isArray(v.sections)),v=>({...v,display_sections:(Array.isArray(v.display_sections)?v.display_sections:v.sections).filter(s=>s&&typeof s==='object'&&typeof s.category==='string')}),null);
 w.mode='run';w.node='taxonomy';w.tab='results';
 w.task={...result[0].value,steps:Array.isArray(result[0].value.steps)?result[0].value.steps.filter(s=>s&&typeof s.name==='string'):[]};state.task=tid;
 Object.assign(w,{artifacts,logs,interpretation,errors});
 await render();
};
actions['wb-refresh']=async()=>{const w=wbState();if(w.mode==='run'&&w.task)return actions['wb-task']({dataset:{id:w.task.id}});wbCapture();return render();};
actions['wb-groups']=async()=>{wbCapture();const key=wbKey(),pid=state.project,value=wbState().draft.dataset_id;if(!value)return notify('请先选择数据集。');const data=await api(`/api/projects/${id(pid)}/datasets/${id(value)}/sample-groups`);if(key!==wbKey()||value!==wbState().draft.dataset_id||state.page!=='analysis')return;modal('样本分组与批次',`<p>当前数据集已保存的分组，只读预览，不修改真实样本信息。</p>${groupTable(data.rows||[],false)}`);};
actions['wb-log']=async el=>{
 const key=wbKey(),tid=wbState().task.id,sequence=wbRequest,request=++wbLogRequest;
 const response=await service('analysis',`/api/tasks/${id(tid)}/log-files/${id(el.dataset.file)}`).then(value=>({value}),error=>({error}));
 if(key!==wbKey()||sequence!==wbRequest||request!==wbLogRequest||state.page!=='analysis'||wbState().mode!=='run'||tid!==wbState().task?.id)return;
 if(response.error)throw response.error;const result=response.value;
 modal('阶段日志预览',pre(typeof result==='string'?result:result?.content??result?.text??result??'日志内容暂不可用'));
};
// Route existing analysis-entry actions into the same workspace, not a raw JSON modal.
const wbPreviousShowTask=showTask;
const wbPreviousTaskResults=actions['task-results'];
showTask=tid=>state.page==='analysis'?actions['wb-task']({dataset:{id:tid}}):wbPreviousShowTask(tid);
actions['task-detail']=el=>showTask(el.dataset.id);
actions['task-results']=el=>state.page==='analysis'?showTask(el.dataset.id):wbPreviousTaskResults(el);
root.addEventListener('input',e=>{if(e.target.closest('[data-form="wb-config"]'))wbCapture();});
root.addEventListener('change',async e=>{
 if(e.target.matches('[data-wb-template]')){wbCapture();const w=wbState();w.template=e.target.value;w.node='data';w.tab='parameters';await render();}
 else if(e.target.matches('[data-wb-project]')){wbCapture();++wbRequest;state.project=e.target.value;state.task='';await render();}
 else if(e.target.name==='dataset_id'||e.target.name==='host_filter_mode'){wbCapture();await render();}
});
forms['wb-config']=async()=>actions['wb-review']();
const wbSignout=actions.signout;
actions.signout=async()=>{++wbRequest;wbStates.clear();await wbSignout();};
