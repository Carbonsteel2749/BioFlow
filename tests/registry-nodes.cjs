const {test}=require('node:test'),assert=require('node:assert/strict');
const M=require('../frontend/workflow-designer-model.js');
test('registered assembly methods become usable catalog entries with independent defaults',()=>{
 const registry={nodes:[{type:'assembly',label:'联合组装',category:'mag',inputs:[{id:'reads',accepted_data_types:['paired_host_removed_reads'],accepted_scopes:['sample'],required:true,multiple:false,collect:true}],outputs:[{id:'assembly',data_type:'assembly_fasta',scope:'cohort'}],parameters_schema:{threads:{type:'integer',default:8},memory_gb:{type:'integer',default:32}},tool_requirements:['MEGAHIT'],database_requirements:[]}]};
 const c=M.catalog(registry);assert.ok(c.assembly);assert.equal(c.assembly.category,'MAG');assert.equal(c.assembly.registered,true);assert.equal(c.assembly.inputs[0].collect,true);
 const g=M.template('blank',c),a=M.addNode(g,'assembly',c),b=M.addNode(g,'assembly',c);
 assert.equal(a.group,'analysis');assert.equal(a.parameters.memory_gb,32);a.parameters.threads=2;assert.equal(b.parameters.threads,8);
 assert.equal(M.importJSON(M.exportJSON(g),{c}).nodes.filter(n=>n.type==='assembly').length,2);
});
test('registered MAG outputs remain connectable to aggregate report',()=>{
 const c=M.catalog({nodes:[{type:'bin_annotation',label:'MAG注释',category:'mag',parameters_schema:{},inputs:[],outputs:[{id:'taxonomy',data_type:'mag_taxonomy',scope:'cohort'}]},{type:'report',inputs:[{id:'artifacts',accepted_data_types:['mag_taxonomy'],accepted_scopes:['sample','cohort'],required:true,multiple:true}],outputs:[{id:'report',data_type:'analysis_report',scope:'cohort'}],parameters_schema:{}}]});
 const g=M.template('blank',c),a=M.addNode(g,'bin_annotation',c),r=M.addNode(g,'report',c);
 assert.equal(M.checkConnection(g,a.id,'taxonomy',r.id,'artifacts',c).ok,true);
});
