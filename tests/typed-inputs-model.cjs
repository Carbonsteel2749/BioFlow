const {test}=require('node:test'),assert=require('node:assert/strict');
const M=require('../frontend/input-datasets-model.js');
test('matrix requires explicit units and normalization; selected estimate deduplicates',()=>{
 const d=M.draft();d.kind='matrix';d.table='asset:a';d.feature_type='species';
 assert.throws(()=>M.spec(d),/单位/);d.unit='fraction';assert.throws(()=>M.spec(d),/标准化/);d.normalization='none';
 assert.equal(M.spec(d).unit,'fraction');assert.equal(M.selectedBytes({kind:'reads',samples:[{read1:'a',read2:'a'}]},[{id:'a',size_bytes:12}]),12);
});
test('processed reads are explicitly confirmed and every stored row validated',()=>{
 const d=M.draft();d.samples=[{sample_id:'s',read1:'asset:a',read2:'asset:b'}];d.processing_status='clean';
 assert.throws(()=>M.spec(d),/说明/);d.processing_note='外部 fastp';assert.equal(M.spec(d).confirmed,undefined);assert.equal(M.spec(d,true).confirmed,true);
 d.samples.push({sample_id:'s',read1:'asset:c',read2:'asset:d'});assert.throws(()=>M.spec(d),/唯一/);
});
test('per-node bindings compare frozen contracts and forbid report with typed input',()=>{
 const g={nodes:[{id:'a',type:'contigs_input'},{id:'b',type:'host_removed_reads_input'}]};
 const registry=[{type:'contigs_input',outputs:[{data_type:'assembly_fasta'}]},{type:'host_removed_reads_input',outputs:[{data_type:'paired_host_removed_reads'}]}];
 const records=[{dataset_id:'x',contract:{data_type:'assembly_fasta'}},{dataset_id:'y',contract:{data_type:'paired_host_removed_reads'}}];
 assert.deepEqual(M.bindings(g,registry,{a:'x',b:'y'},records),{a:'x',b:'y'});assert.throws(()=>M.bindings(g,registry,{a:'y'},records),/不兼容|缺少/);
 g.nodes.push({id:'r',type:'report'});assert.throws(()=>M.bindings(g,registry,{a:'x',b:'y'},records),/REPORT/);
});
