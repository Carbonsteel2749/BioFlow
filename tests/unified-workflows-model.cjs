const {test}=require('node:test'),assert=require('node:assert/strict');
const M=require('../frontend/unified-workflows-model.js');
test('each node instance gets independent parameters from the release',()=>{
 const r={record:{workflow:{nodes:[{id:'a',type:'fastp',parameters:{threads:3}},{id:'b',type:'fastp',parameters:{threads:7}}]}}};
 const values=M.parameters(r);values.a.threads=9;
 assert.equal(values.b.threads,7);assert.equal(r.record.workflow.nodes[0].parameters.threads,3);
});
test('historical graph prefers submitted snapshot over current release',()=>{
 const historical={snapshot:{workflow:{nodes:[{id:'old'}]}}};
 assert.equal(M.history(historical).nodes[0].id,'old');
 assert.throws(()=>M.history({}),/快照/);
});
const graph={nodes:[{id:'input'},{id:'trim'},{id:'tax'},{id:'func'},{id:'report'}],edges:[['input','trim'],['trim','tax'],['trim','func'],['tax','report'],['func','report']].map(([source_node,target_node])=>({source_node,target_node}))};
test('run presentation preserves branches and animates only completed-to-running dependencies',()=>{
 const run={status:'running',nodes:[{node_id:'input',status:'succeeded',progress:100},{node_id:'trim',status:'succeeded',progress:100},{node_id:'tax',status:'running',progress:34},{node_id:'func',status:'queued',progress:0},{node_id:'report',status:'queued',progress:0}]};
 const view=M.describeRun(run,graph,0);
 assert.deepEqual(view.nodes.map(n=>n.depth),[0,1,2,2,3]);assert.equal(view.completed,2);assert.equal(view.percent,47);
 assert.deepEqual(view.edges.filter(e=>e.flow).map(e=>[e.source,e.target]),[['trim','tax']]);assert.equal(view.estimated,true);
 assert.equal(M.describeRun({...run,status:'paused'},graph,0).edges.some(e=>e.flow),false);
 assert.equal(M.describeRun({...run,status:'failed'},graph,0).edges.some(e=>e.flow),false);
});
test('overall success never fabricates missing node success; invalid progress remains unknown',()=>{
 const view=M.describeRun({status:'succeeded',nodes:[{node_id:'input',status:'succeeded'},{node_id:'tax',status:'running',progress:150}]},graph,0);
 assert.equal(view.nodes.find(n=>n.id==='trim').status,'unknown');assert.equal(view.nodes.find(n=>n.id==='tax').percent,null);
 assert.equal(view.completed,1);assert.equal(view.percent,null);assert.equal(view.edges.some(e=>e.flow),false);
 assert.equal(M.describeRun({status:'running',nodes:[{node_id:'tax',status:'running',progress:null}]},graph,0).nodes.find(n=>n.id==='tax').percent,null);
});
test('elapsed time freezes at finish and status labels distinguish failure, cache, and waiting',()=>{
 const start='2026-10-30T00:00:00Z',end='2026-10-30T00:01:30Z';
 assert.equal(M.elapsed({started_at:start,finished_at:end},Date.parse(end)+60000),90);
 assert.equal(M.elapsed({started_at:start},Date.parse(end)),90);assert.equal(M.elapsed({},0),null);
 assert.equal(M.status('cached').label,'缓存复用');assert.equal(M.status('failed').label,'失败');assert.equal(M.status('queued').label,'排队中');assert.equal(M.status('not-a-state').kind,'unknown');
});
test('a stopped record without a finish timestamp does not keep inventing elapsed time',()=>{
 const started_at='2026-10-30T00:00:00Z',updated_at='2026-10-30T00:02:00Z';
 assert.equal(M.elapsed({status:'paused',started_at,updated_at},Date.parse(updated_at)+60000),120);
 assert.equal(M.elapsed({status:'failed',started_at},Date.parse(updated_at)+60000),null);
});
