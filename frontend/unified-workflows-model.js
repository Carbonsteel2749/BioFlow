(function(root){
 const clone=x=>JSON.parse(JSON.stringify(x));
 const M={parameters:r=>Object.fromEntries(r.record.workflow.nodes.map(n=>[n.id,clone(n.parameters||{})])),
 history:detail=>{const w=detail?.snapshot?.workflow;if(!w?.nodes)throw Error('历史流程快照不可用，不能用最新模板代替');return clone(w);}};
 const states={queued:['queued','排队中'],pending:['pending','待运行'],running:['running','运行中'],succeeded:['succeeded','已完成'],completed:['succeeded','已完成'],cached:['cached','缓存复用'],failed:['failed','失败'],paused:['paused','已暂停'],cancelled:['cancelled','已取消'],cancelling:['cancelling','正在取消'],skipped:['skipped','已跳过']};
 M.status=value=>{const s=states[value]||['unknown','状态待确认'];return {kind:s[0],label:s[1]};};
 M.elapsed=(record,now=Date.now())=>{const start=Date.parse(record?.started_at);let end=Date.parse(record?.finished_at);if(!Number.isFinite(end)&&record?.status&&!['running','queued','pending','cancelling'].includes(record.status)){end=Date.parse(record.updated_at);if(!Number.isFinite(end))return null;}return Number.isFinite(start)?Math.max(0,Math.floor(((Number.isFinite(end)?end:now)-start)/1000)):null;};
 M.describeRun=(run={},workflow={},now=Date.now())=>{
  const records=Array.isArray(run.nodes)?run.nodes:Array.isArray(run.node_states)?run.node_states:[],byId=new Map(records.map(n=>[n.node_id,n]));
  const definitions=workflow.nodes||[],known=new Set(definitions.map(n=>n.id)),depth=new Map(definitions.map(n=>[n.id,0]));
  const edges=(workflow.edges||[]).map(e=>({source:e.source_node,target:e.target_node})).filter(e=>known.has(e.source)&&known.has(e.target));
  const incoming=new Map(definitions.map(n=>[n.id,0]));for(const e of edges)incoming.set(e.target,incoming.get(e.target)+1);
  const queue=definitions.filter(n=>incoming.get(n.id)===0).map(n=>n.id);
  for(let i=0;i<queue.length;i++)for(const e of edges.filter(e=>e.source===queue[i])){depth.set(e.target,Math.max(depth.get(e.target),depth.get(e.source)+1));incoming.set(e.target,incoming.get(e.target)-1);if(incoming.get(e.target)===0)queue.push(e.target);}
  const status=M.status(run.status),done=s=>['succeeded','cached'].includes(s);
  const nodes=definitions.map(n=>{const record=byId.get(n.id)||{},state=M.status(record.status);const p=record.progress;return {...n,record,status:state.kind,label:state.label,percent:done(state.kind)?100:typeof p==='number'&&Number.isFinite(p)&&p>=0&&p<=100?p:null,depth:depth.get(n.id),seconds:M.elapsed(record,now)};});
  const mapped=new Map(nodes.map(n=>[n.id,n]));return {status:status.kind,label:status.label,nodes,edges:edges.map(e=>({...e,flow:status.kind==='running'&&done(mapped.get(e.source).status)&&mapped.get(e.target).status==='running'})),completed:nodes.filter(n=>done(n.status)).length,total:nodes.length,active:nodes.filter(n=>n.status==='running'),percent:nodes.length&&nodes.every(n=>n.percent!==null)?Math.round(nodes.reduce((sum,n)=>sum+n.percent,0)/nodes.length):null,estimated:true,seconds:M.elapsed(run,now),error:run.error_message||null};
 };
 if(typeof module==='object'&&module.exports)module.exports=M;else root.BioFlowUnified=M;
})(typeof window==='object'?window:globalThis);
