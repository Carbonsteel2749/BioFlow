const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const file=path.join(__dirname,'../frontend/analysis-workbench-model.js');
test('workflow status does not infer node success from overall task success',()=>{
 assert.ok(fs.existsSync(file),'workbench model has not been implemented');
 const m=require(file);
 assert.equal(m.nodeStatus({status:'completed',steps:[]},'taxonomy'),'unknown');
 assert.equal(m.nodeStatus({steps:[null]},'taxonomy'),'unknown');
 assert.equal(m.nodeStatus({steps:{}},'taxonomy'),'unknown');
 assert.equal(m.nodeStatus({steps:[{name:'fastqc_raw',status:'succeeded'},{name:'fastp',status:'running'}]},'qc'),'running');
 assert.equal(m.nodeStatus({steps:[{name:'fastqc_raw',status:'succeeded'},{name:'fastp',status:'failed'}]},'qc'),'failed');
});
test('artifact filtering keeps pathways separate from genes and rejects a different task',()=>{
 assert.ok(fs.existsSync(file),'workbench model has not been implemented');
 const m=require(file);
 const artifacts=[
  {task_id:'t',node_id:'functional_annotation',file_name:'KO.tsv'},
  {task_id:'t',node_id:'functional_annotation',file_name:'pathway_abundance.tsv'},
  {task_id:'other',node_id:'taxonomy',file_name:'species_abundance.tsv'},
  {task_id:'t',node_id:'taxonomy_plots',file_name:'taxonomy_alpha.png'}
 ];
 assert.deepEqual(m.nodeArtifacts(artifacts,'functional','t').map(a=>a.file_name),['KO.tsv']);
 assert.deepEqual(m.nodeArtifacts(artifacts,'pathways','t').map(a=>a.file_name),['pathway_abundance.tsv']);
 assert.deepEqual(m.nodeArtifacts(artifacts,'taxonomy','t').map(a=>a.file_name),['taxonomy_alpha.png']);
});
test('paper template remains unexecutable and does not reuse standard method claims',()=>{
 assert.ok(fs.existsSync(file),'workbench model has not been implemented');
 const m=require(file);
 assert.equal(m.templates.paper.executable,false);
 assert.match(m.templates.paper.nodes.find(n=>n.id==='taxonomy').tool,/MOCAT/);
 assert.ok(m.templates.paper.nodes.find(n=>n.id==='catalog'));
 assert.match(m.templates.standard.nodes.find(n=>n.id==='taxonomy').tool,/Kraken2/);
});
