const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function setup(){const pending=new Map(),stats={render:0,select:0};let binding;
 const context={window:{BioFlowUnified:require('../frontend/unified-workflows-model.js'),BioFlowRunUI:{bind:x=>binding=x,select:()=>stats.select++}},bioflowUser:{id:'u'},state:{project:'p',page:'analysis'},generation:0,views:{analysis:()=>''},actions:{},forms:{},root:{addEventListener(){}},$:()=>null,pa:x=>x,id:encodeURIComponent,esc:String,wbState:()=>({mode:'configure'}),render:async()=>stats.render++,api:url=>new Promise(resolve=>pending.set(url,resolve))};vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(__dirname,'../frontend/unified-workflows.js'),'utf8'),context);
 const detail=task_id=>({run:{task_id,status:'running'},snapshot:{workflow:{nodes:[{id:'a',type:'fastp',parameters:{}},{id:'b',type:'fastp',parameters:{}}],edges:[]},parameter_registry:{nodes:[]}}});
 return {context,binding:()=>binding,pending,stats,detail};}
test('latest history intent wins and returning to creation invalidates outstanding selection',async()=>{
 const x=setup(),a=x.context.actions['unified-history']({dataset:{id:'a'}}),b=x.context.actions['unified-history']({dataset:{id:'b'}});
 x.pending.get('/analysis/workflows/runs/b')(x.detail('b'));await b;x.pending.get('/analysis/workflows/runs/a')(x.detail('a'));await a;
 assert.equal(x.binding().current().history.run.task_id,'b');
 const c=x.context.actions['unified-history']({dataset:{id:'c'}});await x.context.actions['unified-new']();x.pending.get('/analysis/workflows/runs/c')(x.detail('c'));await c;assert.equal(x.binding().current().history,null);
});
test('node selection in retained history does not fetch or re-render the whole workspace',async()=>{
 const x=setup();x.binding().current().history=x.detail('a');const before=x.stats.render;
 await x.context.actions['unified-node']({dataset:{node:'b'}});assert.equal(x.binding().current().node,'b');assert.equal(x.stats.render,before);assert.equal(x.stats.select,1);assert.equal(x.pending.size,0);
});
test('a request started before navigation cannot reopen a history view',async()=>{
 const x=setup(),pending=x.context.actions['unified-history']({dataset:{id:'a'}});x.context.generation++;
 x.pending.get('/analysis/workflows/runs/a')(x.detail('a'));await pending;assert.equal(x.binding().current().history,null);
});
