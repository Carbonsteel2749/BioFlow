const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function setup(responses){
 const handlers={},posts=[];let counter=0,details=0;
 const context={window:{BioFlowInputs:require('../frontend/input-datasets-model.js')},bioflowUser:{id:'u'},state:{project:'p',page:'input-datasets'},actions:{signout:async()=>{}},forms:{},views:{},names:{},root:{addEventListener:(name,fn)=>handlers[name]=fn},document:{hidden:false,addEventListener(){}},chrome:()=>{},navigate:()=>{},render:async()=>{},$:()=>null,esc:String,id:encodeURIComponent,pa:s=>'/p'+s,button:()=>'',crypto:{randomUUID:()=>`key-${++counter}`},setTimeout:()=>1,clearTimeout:()=>{},URLSearchParams,
 api:async(url,method,body)=>{if(method==='POST'){posts.push(JSON.parse(JSON.stringify(body)));const next=responses.shift();if(next instanceof Error)throw next;return next;}if(url.includes('/imports/'))return {id:'job',dataset_id:'job',status:'ready'};details++;if(details===1)throw Error('临时元数据读取失败');return {dataset_id:'job',contract:{kind:'reads'}};}};
 vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../frontend/input-datasets.js'),'utf8'),context);
 for(const [name,value] of Object.entries({sample_id:'s',read1:'asset:a',read2:'asset:b'}))handlers.input({target:{dataset:{inputRow:name},value,closest:()=>({dataset:{row:'0'}}),matches:()=>false}});
 return {context,posts,details:()=>details};
}
test('failed job replacement retains exact idempotent request after uncertain response',async()=>{
 const x=setup([{id:'failed',status:'failed'},new TypeError('lost response'),{id:'replacement',status:'queued'}]);
 await x.context.forms['input-import']();await x.context.forms['input-import']();await x.context.forms['input-import']();
 assert.notEqual(x.posts[0].idempotency_key,x.posts[1].idempotency_key);assert.deepEqual(x.posts[1],x.posts[2]);
});
test('ready import metadata can be retried without a new import or integrity claim',async()=>{
 const x=setup([{id:'job',dataset_id:'job',status:'queued'}]);await x.context.forms['input-import']();await x.context.actions['input-poll']();assert.equal(x.details(),1);await x.context.actions['input-poll']();assert.equal(x.details(),2);assert.equal(x.posts.length,1);
});
