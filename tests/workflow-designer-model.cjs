const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const file=path.join(__dirname,'../frontend/workflow-designer-model.js');
function model(){assert.ok(fs.existsSync(file),'visual workflow model is not implemented');return require(file);}
test('standard template has real data branches and separate disabled repair policy',()=>{
 const m=model(),g=m.template('metagenomics');
 assert.equal(g.nodes.find(n=>n.type==='repair_llm').parameters.enabled,false);
 assert.equal(g.nodes.filter(n=>n.type==='interpret_llm').length,1);
 assert.ok(g.nodes.some(n=>n.type==='analysis_group'));
 assert.equal(m.validate(g).errors.length,0);
 const qc=g.nodes.find(n=>n.type==='fastqc'),clean=g.nodes.find(n=>n.type==='fastp');
 assert.equal(g.edges.some(e=>e.source===qc.id&&e.target===clean.id),false);
 assert.equal(m.checkConnection(g,qc.id,'report',clean.id,'reads').ok,false);
});
test('repeat nodes have independent parameters and deterministic import/export',()=>{
 const m=model(),g=m.template('blank'),a=m.addNode(g,'fastp'),b=m.addNode(g,'fastp');
 a.parameters.length_required=71;
 assert.notEqual(a.id,b.id);assert.equal(b.parameters.length_required,50);
 const copy=m.importJSON(m.exportJSON(g));
 assert.equal(copy.nodes.find(n=>n.id===a.id).parameters.length_required,71);
 assert.equal(copy.nodes.find(n=>n.id===b.id).parameters.length_required,50);
});
test('connection checks reject cycles, duplicate and full ports; raw annotation requires acknowledgement',()=>{
 const m=model(),g=m.template('blank'),raw=g.nodes.find(n=>n.type==='fastq_input');
 const t=m.addNode(g,'taxonomy');
 assert.equal(m.checkConnection(g,raw.id,'reads',t.id,'reads').warning.code,'raw_annotation');
 assert.throws(()=>m.connect(g,raw.id,'reads',t.id,'reads'),/确认/);
 m.connect(g,raw.id,'reads',t.id,'reads',true);
 assert.equal(m.checkConnection(g,raw.id,'reads',t.id,'reads').ok,false);
 const a=m.addNode(g,'report'),b=m.addNode(g,'figures');
 m.connect(g,t.id,'abundance',a.id,'artifacts');m.connect(g,a.id,'report',b.id,'results');
 assert.equal(m.checkConnection(g,b.id,'figures',a.id,'artifacts').ok,false);
 assert.equal(m.checkConnection(g,t.id,'abundance',raw.id,'control').ok,false);
});
test('invalid parameter values and unknown custom mappings remain visible in validation',()=>{
 const m=model(),g=m.template('metagenomics'),n=g.nodes.find(n=>n.type==='fastp');
 n.parameters.threads=-1;
 assert.ok(m.validate(g).errors.some(x=>x.node===n.id&&/线程/.test(x.message)));
 n.parameters.threads=4;n.custom=[{name:'extra_flag',type:'number',value:2,description:'test'}];
 assert.ok(m.validate(g).warnings.some(x=>x.code==='custom_unmapped'));
 n.custom.push({name:'threads',type:'number',value:2,description:''});
 assert.ok(m.validate(g).errors.some(x=>/重复/.test(x.message)));
});
test('16S design never presents metagenomic functional inference as available',()=>{
 const m=model(),g=m.template('amplicon16s');
 assert.ok(g.nodes.some(n=>n.type==='dada2'));
 assert.ok(!g.nodes.some(n=>n.type==='functional_annotation'));
 assert.ok(m.validate(g).warnings.some(x=>x.code==='design_only'));
 assert.equal(g.execution,'design_only');
});
test('import fails closed for corrupt graphs, credentials, duplicate IDs and huge input',()=>{
 const m=model(),g=m.template('blank');
 assert.throws(()=>m.importJSON('{'),/JSON/);
 assert.throws(()=>m.importJSON(' '.repeat(2100000)),/大小/);
 const bad=structuredClone(g);bad.nodes.push(structuredClone(bad.nodes[0]));
 assert.throws(()=>m.importJSON(JSON.stringify(bad)),/重复/);
 const secret=structuredClone(g);secret.nodes[0].parameters.api_key='secret';
 assert.throws(()=>m.importJSON(JSON.stringify(secret)),/凭据|未知参数/);
 const position=structuredClone(g);position.nodes[0].x='url(x)';
 assert.throws(()=>m.importJSON(JSON.stringify(position)),/坐标/);
 const proto=m.exportJSON(g).replace('"parameters": {}','"parameters": {"__proto__":{}}');
 assert.throws(()=>m.importJSON(proto),/非法/);
});
test('portable exports strip project bindings but local drafts retain them',()=>{
 const m=model(),g=m.template('blank');g.nodes.find(n=>n.type==='fastq_input').parameters.dataset_id='project-A-data';
 assert.equal(m.importJSON(m.exportJSON(g)).nodes.find(n=>n.type==='fastq_input').parameters.dataset_id,'');
 assert.equal(m.importJSON(m.exportJSON(g,{portable:false}),{portable:false}).nodes.find(n=>n.type==='fastq_input').parameters.dataset_id,'project-A-data');
 assert.notEqual(m.draftKey('ab','c'),m.draftKey('a','bc'));
});
test('registry schemas retain server bounds and require compatible scope',()=>{
 const m=model(),catalog=m.catalog({nodes:[{type:'fastp',label:'fastp',parameters_schema:{type:'object',properties:{threads:{type:'integer',minimum:1,maximum:8,default:2}}}}]});
 assert.equal(catalog.fastp.parameters.threads.maximum,8);
 assert.match(catalog.fastp.parameters.threads.title,/线程/);
 assert.ok(catalog.fastp.parameters.threads.description);
 const g=m.template('metagenomics',catalog);g.nodes.find(n=>n.type==='fastp').parameters.threads=9;
 assert.ok(m.validate(g,catalog).errors.some(x=>/线程/.test(x.message)));
});
test('exports refuse credentials in custom parameters instead of persisting them',()=>{
 const m=model(),g=m.template('qc');g.nodes.find(n=>n.type==='fastp').custom=[{name:'api_key',type:'string',value:'not-a-real-key',description:''}];
 assert.throws(()=>m.exportJSON(g),/凭据/);
 assert.throws(()=>m.exportJSON(g,{portable:false}),/凭据/);
});
