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
