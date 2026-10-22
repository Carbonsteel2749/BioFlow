/* Native controls for the existing workflow graph API. */
names.workflows='流程设计';
let workflowState={graph:null,registry:null,template:null};
const standardAnalysis=views.analysis;
views.analysis=async()=>{const html=await standardAnalysis();return html.replace('<div class="split">',`<div class="row workflow-toolbar">${button('流程模板与节点设计','workflows')}${button('资源与运行环境','resources')}</div><div class="split">`);};
actions.workflows=()=>navigate('workflows');
actions.resources=async()=>modal('分析运行环境',pre(await service('analysis','/api/runtime/environment')));
const nodeDefinition=node=>(workflowState.registry.nodes||[]).find(d=>d.type===node.type);
const nodeParameters=node=>{const schema=nodeDefinition(node)?.parameters_schema||{};return schema.properties||schema;};
function parameterControl(node,key,schema){
 const value=node.parameters[key]??schema.default??'';
 const name=`param:${node.id}:${key}`;
 if(schema.enum)return field(esc(schema.title||key),select(name,schema.enum.map(v=>[v,String(v)])) .replace(`value="${esc(value)}"`,`value="${esc(value)}" selected`));
 if(schema.type==='boolean')return `<label><input name="${esc(name)}" type="checkbox" ${value?'checked':''}> ${esc(key)}</label>`;
 return field(esc(schema.title||key),`<input name="${esc(name)}" type="${['integer','number'].includes(schema.type)?'number':'text'}" value="${esc(value)}" ${schema.type==='number'?'step="any"':''}>`);
}
views.workflows=async()=>{
 const [templates,registry]=await Promise.all([service('analysis','/api/workflows/templates'),service('analysis','/api/workflows/registry')]);workflowState.registry=registry;
 let html=head('流程模板与节点设计','加载已有模板，调整节点参数与连接，再提交现有流程引擎。')+`<div class="grid two">${items(templates).map(t=>`<div class="box"><h2>${esc(t.name)}</h2><p>${esc(t.description)}</p>${button('载入模板','load-template',`data-id="${esc(t.template_id||t.id)}"`)}</div>`).join('')}</div>`;
 const graph=workflowState.graph;if(!graph)return html;
 html+=`<form data-form="workflow"><div class="box">${field('流程名称',input('workflowName','流程名称',workflowState.template?.name||current().name))}${field('服务器样本清单路径',input('manifest','manifest 路径',state.manifest||''))}</div><div class="grid two">${graph.nodes.map(n=>{const def=nodeDefinition(n);const params=nodeParameters(n);return `<div class="box"><span class="tag">${esc(n.id)}</span><h2>${esc(def?.label||n.type)}</h2>${Object.entries(params).map(([k,s])=>parameterControl(n,k,s)).join('')}${button('移除此节点','remove-node',`data-id="${esc(n.id)}"`)}</div>`;}).join('')}</div><div class="box"><h2>节点连接</h2>${graph.edges.map(e=>`<p>${esc(e.source_node)} · ${esc(e.source_port)} → ${esc(e.target_node)} · ${esc(e.target_port)} ${button('移除连接','remove-edge',`data-id="${esc(e.id)}"`)}</p>`).join('')}<div class="row">${button('增加节点','add-node')}${button('连接节点','add-edge')}</div></div><div class="row"><button class="primary" name="intent" value="run">确认并执行流程</button><button name="intent" value="save">另存为流程模板</button></div></form>`;return html;
};
actions['load-template']=async el=>{const t=await service('analysis',`/api/workflows/templates/${id(el.dataset.id)}`);workflowState.template=t;workflowState.graph=structuredClone(t.workflow);await render();};
function captureWorkflow(){const form=$('[data-form="workflow"]');if(!form)return;for(const node of workflowState.graph.nodes){const props=nodeParameters(node);for(const [key,schema] of Object.entries(props)){const el=form.elements.namedItem(`param:${node.id}:${key}`);if(!el)continue;if(schema.type==='boolean')node.parameters[key]=el.checked;else if(el.value!=='')node.parameters[key]=['integer','number'].includes(schema.type)?Number(el.value):schema.enum?.find(v=>String(v)===el.value)??el.value;else delete node.parameters[key];}}state.manifest=form.elements.namedItem('manifest').value;}
actions['remove-node']=async el=>{captureWorkflow();const g=workflowState.graph;g.nodes=g.nodes.filter(n=>n.id!==el.dataset.id);g.edges=g.edges.filter(e=>e.source_node!==el.dataset.id&&e.target_node!==el.dataset.id);await render();};
actions['remove-edge']=async el=>{captureWorkflow();workflowState.graph.edges=workflowState.graph.edges.filter(e=>e.id!==el.dataset.id);await render();};
actions['add-node']=()=>{captureWorkflow();modal('增加流程节点',`<form data-form="node">${field('节点类型',select('type',(workflowState.registry.nodes||[]).map(d=>[d.type,d.label])))}${field('节点编号',input('nodeId','例如 qc_2'))}<button class="primary">增加节点</button></form>`);};
forms.node=async f=>{const type=f.elements.namedItem('type').value;const nid=f.elements.namedItem('nodeId').value;if(!/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(nid)||workflowState.graph.nodes.some(n=>n.id===nid))throw Error('节点编号须唯一，使用字母、数字、点、下划线或短横线');workflowState.graph.nodes.push({id:nid,type,parameters:{}});$('.overlay').hidden=true;await render();};
actions['add-edge']=()=>{captureWorkflow();const g=workflowState.graph;const sources=g.nodes.flatMap(n=>(nodeDefinition(n)?.outputs||[]).map(p=>[n.id+':'+p.id,n.id+' · '+p.id]));const targets=g.nodes.flatMap(n=>(nodeDefinition(n)?.inputs||[]).map(p=>[n.id+':'+p.id,n.id+' · '+p.id]));modal('连接流程节点',`<form data-form="edge">${field('输出端口',select('source',sources))}${field('输入端口',select('target',targets))}<button class="primary">增加连接</button></form>`);};
forms.edge=async f=>{const [source_node,source_port]=f.elements.namedItem('source').value.split(':');const [target_node,target_port]=f.elements.namedItem('target').value.split(':');if(!source_node||!target_node)throw Error('请选择完整的输入输出端口');workflowState.graph.edges.push({id:'edge_'+Date.now(),source_node,source_port,target_node,target_port});$('.overlay').hidden=true;await render();};
forms.workflow=async f=>{captureWorkflow();const name=f.elements.namedItem('workflowName').value;const workflow=workflowState.graph;if(state.workflowIntent==='save'){await service('analysis','/api/workflows/templates','POST',{name,workflow});notify('流程模板已保存');}else{const run=await service('analysis','/api/workflows/runs','POST',{name,manifest_path:state.manifest,workflow});await api(pa('/links/workflow'),'POST',{id:run.task_id});notify('流程已提交：'+run.task_id);}await render();};
root.addEventListener('click',e=>{if(e.target.name==='intent')state.workflowIntent=e.target.value;},true);
const priorBoot=views.overview;
// Workflow runs are saved separately from standard tasks because their contracts differ.
const priorAnalysis=views.analysis;
views.analysis=async()=>{let html=await priorAnalysis();const ids=await api(pa('/links/workflow'));if(!ids.length)return html;const runs=await Promise.all(ids.map(t=>service('analysis',`/api/workflows/runs/${id(t)}`)));return html+`<div class="box"><h2>项目流程任务</h2>${runs.map(r=>`<p>${esc(r.name||r.task_id)} · ${esc(r.status)} ${button('流程进度','workflow-progress',`data-id="${esc(r.task_id)}"`)}</p>`).join('')}</div>`;};
actions['workflow-progress']=async el=>{const tid=el.dataset.id;modal('流程运行记录',pre(await service('analysis',`/api/workflows/runs/${id(tid)}`))+button('刷新进度','workflow-progress',`data-id="${esc(tid)}"`)+button('恢复执行','workflow-resume',`data-id="${esc(tid)}"`)+button('取消流程','workflow-cancel',`data-id="${esc(tid)}"`));};
actions['workflow-resume']=async el=>{await service('analysis',`/api/workflows/runs/${id(el.dataset.id)}/resume`,'POST',{});await actions['workflow-progress'](el);};
actions['workflow-cancel']=async el=>{await service('analysis',`/api/workflows/runs/${id(el.dataset.id)}/cancel`,'POST',{});await actions['workflow-progress'](el);};
