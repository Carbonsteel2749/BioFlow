(function(root){
 const clone=x=>JSON.parse(JSON.stringify(x));
 const M={parameters:r=>Object.fromEntries(r.record.workflow.nodes.map(n=>[n.id,clone(n.parameters||{})])),
 history:detail=>{const w=detail?.snapshot?.workflow;if(!w?.nodes)throw Error('历史流程快照不可用，不能用最新模板代替');return clone(w);}};
 if(typeof module==='object'&&module.exports)module.exports=M;else root.BioFlowUnified=M;
})(typeof window==='object'?window:globalThis);
