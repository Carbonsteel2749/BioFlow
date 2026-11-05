const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function setup(responses,options={}){
 const handlers={},posts=[];let counter=0,details=0;
 const storage=options.storage||new Map();
 const cryptoApi={getRandomValues:a=>require('node:crypto').webcrypto.getRandomValues(a)};
 const context={window:{BioFlowInputs:require('../frontend/input-datasets-model.js')},bioflowUser:{id:'u'},state:{project:'p',page:'input-datasets'},actions:{signout:async()=>{}},forms:{},views:{},names:{},root:{addEventListener:(name,fn)=>handlers[name]=fn},document:{hidden:false,addEventListener(){}},chrome:()=>{},navigate:()=>{},render:async()=>{},$:()=>null,esc:String,id:encodeURIComponent,pa:s=>'/p'+s,button:(label,action,attrs)=>`<button data-action="${action}" ${attrs||''}>${label}</button>`,crypto:{randomUUID:()=>`key-${++counter}`,subtle:require('node:crypto').webcrypto.subtle},TextEncoder,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k),key:i=>[...storage.keys()][i],get length(){return storage.size}},setTimeout:()=>1,clearTimeout:()=>{},URLSearchParams,
 api:async(url,method,body)=>{if(method==='POST'){posts.push(JSON.parse(JSON.stringify(body)));const next=responses.shift();if(next instanceof Error)throw next;return next;}if(url.endsWith('/candidates'))return {items:[],catalog:{}};if(url.endsWith('/input-datasets'))return options.list?options.list():{items:options.history||[]};if(url.includes('/imports/'))return {id:'job',dataset_id:'job',status:options.pollStatus||'ready'};details++;if(details===1)throw Error('临时元数据读取失败');return {dataset_id:'job',contract:{kind:'reads'}};}};
 context.crypto=cryptoApi;
 vm.createContext(context);vm.runInContext(fs.readFileSync(require.resolve('../frontend/input-datasets.js'),'utf8'),context);
 for(const [name,value] of Object.entries({sample_id:'s',read1:'asset:a',read2:'asset:b'}))handlers.input({target:{dataset:{inputRow:name},value,closest:()=>({dataset:{row:'0'}}),matches:()=>false}});
 let dialog=null;
 context.root.append=d=>{dialog=d;};
 context.document.createElement=()=>({dataset:{},setAttribute(){},addEventListener(){},showModal(){},close(){},remove(){dialog=null;}});
 context.$=selector=>selector==='[data-input-recovery-review]'?dialog:null;
 return {context,posts,storage,details:()=>details,dialog:()=>dialog};
}
test('failed job replacement retains exact idempotent request after uncertain response',async()=>{
 const x=setup([{id:'failed',status:'failed'},new TypeError('lost response'),{id:'replacement',status:'queued'}]);
 await x.context.forms['input-import']();await x.context.forms['input-import']();await x.context.forms['input-import']();
 assert.notEqual(x.posts[0].idempotency_key,x.posts[1].idempotency_key);assert.deepEqual(x.posts[1],x.posts[2]);
});
test('reload lists queued and failed persisted imports and ready detail remains retryable',async()=>{
 const history=[{id:'job',dataset_id:'job',status:'queued'},{id:'bad',dataset_id:'bad',status:'failed',error:'配对文件不匹配'},{id:'ready',dataset_id:'ready',status:'ready'}];
 const x=setup([],{history});const html=await x.context.views['input-datasets']();assert.match(html,/配对文件不匹配/);assert.match(html,/job/);
 await x.context.actions['input-job']({dataset:{job:'job'}});assert.equal(x.details(),1);
 await x.context.actions['input-poll']();assert.equal(x.details(),2);assert.equal(x.posts.length,0);
});
test('accepted lost POST is reconciled after reload by scoped opaque identity, never reposted',async()=>{
 const x=setup([new TypeError('lost')]);await x.context.forms['input-import']();assert.equal(x.storage.size,1);
 const request_id=x.posts[0].idempotency_key.slice('typedimport_'.length);
 const reload=setup([],{storage:x.storage,history:[{id:'accepted',dataset_id:'accepted',status:'validating',request_id}]});
 const html=await reload.context.views['input-datasets']();assert.match(html,/accepted/);assert.match(html,/正在复制并校验/);assert.equal(reload.posts.length,0);assert.equal(reload.storage.size,0);
});
test('history is bounded and context changes reject late responses',async()=>{
 let resolve;const x=setup([],{list:()=>new Promise(r=>resolve=r)});const pending=x.context.views['input-datasets']();await new Promise(r=>setImmediate(r));
 x.context.state.project='other';resolve({items:[{id:'old-secret',status:'failed',error:'old error'}]});assert.equal(await pending,'');
 const y=setup([],{history:Array.from({length:30},(_,i)=>({id:`row-${i}`,status:'failed',error:`error-${i}`}))});
 let html=await y.context.views['input-datasets']();assert(!html.includes('error-29'));await y.context.actions['input-history-more']();html=await y.context.views['input-datasets']();assert(html.includes('error-29'));
});
test('reload recovery retains warning on unavailable history and tolerates corrupt storage',async()=>{
 const k='bioflow-input-pending:'+JSON.stringify(['u','p']),storage=new Map([[k,'a'.repeat(64)]]);
 const x=setup([],{storage,list:async()=>{throw Error('历史服务暂不可用');}});
 const html=await x.context.views['input-datasets']();assert.match(html,/网络结果仍待确认/);assert.match(html,/不能据此判定无导入/);
 await x.context.forms['input-import']();assert.equal(x.posts.length,0);assert.equal(storage.size,1);
 const bad=new Map([[k,'{'.repeat(100000)]]),y=setup([],{storage:bad});await y.context.views['input-datasets']();assert.equal(bad.size,0);
});
test('ready history selection retries detail; signout clears only account markers; late POST cannot select another account job',async()=>{
 const x=setup([],{history:[{id:'ready',dataset_id:'ready',status:'ready'}]});await x.context.views['input-datasets']();await x.context.actions['input-job']({dataset:{job:'ready'}});assert.equal(x.details(),1);await x.context.actions['input-poll']();assert.equal(x.details(),2);
 const own='bioflow-input-pending:'+JSON.stringify(['u','p']),other='bioflow-input-pending:'+JSON.stringify(['v','p']);x.storage.set(own,'a'.repeat(64));x.storage.set(other,'b'.repeat(64));await x.context.actions.signout();assert(!x.storage.has(own));assert(x.storage.has(other));
 let resolve;const y=setup([new Promise(r=>resolve=r)]);const pending=y.context.forms['input-import']();await new Promise(r=>setImmediate(r));y.context.bioflowUser={id:'v'};resolve({id:'oldjob',status:'queued'});await pending;const html=await y.context.views['input-datasets']();assert(!html.includes('oldjob'));assert.equal(y.posts.length,1);
});
test('ready import metadata can be retried without a new import or integrity claim',async()=>{
 const x=setup([{id:'job',dataset_id:'job',status:'queued'}]);await x.context.forms['input-import']();await x.context.actions['input-poll']();assert.equal(x.details(),1);await x.context.actions['input-poll']();assert.equal(x.details(),2);assert.equal(x.posts.length,1);
});
test('context switch during submission rendering cannot POST into a different project',async()=>{
 const x=setup([{id:'wrong-project',status:'queued'}]);x.context.render=async()=>{x.context.state.project='other';};
 await x.context.forms['input-import']();assert.equal(x.posts.length,0);assert.equal(x.storage.size,0);
});
const markerKey='bioflow-input-pending:'+JSON.stringify(['u','p']);
test('real submission persists opaque marker before rendering and before issuing POST',async()=>{
 let resume;const x=setup([]);x.context.render=()=>new Promise(r=>resume=r);
 const pending=x.context.forms['input-import']();await new Promise(r=>setImmediate(r));
 assert.equal(x.posts.length,0);assert.match(x.storage.get(markerKey),/^[a-f0-9]{64}$/);
 const reload=setup([],{storage:new Map(x.storage)});await reload.context.views['input-datasets']();
 await reload.context.actions['input-recovery-start']();await reload.context.actions['input-recovery-confirm']();
 assert.equal(reload.storage.size,0);assert.equal(reload.posts.length,0);
 x.context.state.project='other';resume();await pending;
});
test('pre-POST marker and repeated no-match history require explicit cancellable recovery, never automatic POST',async()=>{
 const storage=new Map([[markerKey,'a'.repeat(64)]]);let reads=0;
 const x=setup([{id:'new',status:'queued'}],{storage,list:async()=>{reads++;return {items:[]};}});
 let html=await x.context.views['input-datasets']();assert.match(html,/input-recovery-start/);
 await x.context.actions['input-history-refresh']();await x.context.actions['input-history-refresh']();
 assert.equal(storage.size,1);await x.context.forms['input-import']();assert.equal(x.posts.length,0);
 await x.context.actions['input-recovery-start']();assert(x.dialog());assert.match(x.dialog().innerHTML,/重复/);
 await x.context.actions['input-recovery-cancel']();assert.equal(storage.size,1);assert.equal(x.dialog(),null);
 await x.context.actions['input-recovery-start']();await x.context.actions['input-recovery-confirm']();
 assert.equal(storage.size,0);assert.equal(x.posts.length,0);assert(reads>=6);
 html=await x.context.views['input-datasets']();assert(!html.includes('input-recovery-start'));
 await x.context.forms['input-import']();assert.equal(x.posts.length,1);
});
test('network never reaches server then reload can recover without replaying the old request',async()=>{
 const x=setup([new TypeError('offline')]);await x.context.forms['input-import']();
 const reload=setup([],{storage:x.storage});await reload.context.views['input-datasets']();
 await reload.context.actions['input-recovery-start']();await reload.context.actions['input-recovery-confirm']();
 assert.equal(reload.storage.size,0);assert.equal(reload.posts.length,0);
});
test('recovery must freshly reconcile both before dialog and confirmation; unavailable or accepted-late cannot abandon',async()=>{
 for(const stage of ['start-unavailable','confirm-unavailable','accepted-start','accepted-late']){
  let reads=0;const storage=new Map([[markerKey,'a'.repeat(64)]]);
  const x=setup([],{storage,list:async()=>{reads++;if(stage==='start-unavailable'&&reads===2||stage==='confirm-unavailable'&&reads===3)throw Error('unavailable');return {items:stage==='accepted-late'&&reads===3||stage==='accepted-start'&&reads===2?[{id:'accepted',status:'validating',request_id:'a'.repeat(64)}]:[]};}});
  await x.context.views['input-datasets']();await x.context.actions['input-recovery-start']();
  if(stage==='accepted-start'){assert.equal(x.dialog(),null);assert.equal(storage.size,0);assert.match(await x.context.views['input-datasets'](),/accepted/);}
  else if(stage==='start-unavailable'){assert.equal(x.dialog(),null);assert.equal(storage.size,1);}
  else {await x.context.actions['input-recovery-confirm']();if(stage==='accepted-late'){assert.equal(storage.size,0);assert.match(await x.context.views['input-datasets'](),/accepted/);}else assert.equal(storage.size,1);}
  assert.equal(x.posts.length,0);
 }
});
test('recovery cannot clear a changed marker or another project/account, including changes during fresh lookup',async()=>{
 for(const change of ['marker','project','account']){
  let resolve,reads=0;const storage=new Map([[markerKey,'a'.repeat(64)]]);
  const x=setup([],{storage,list:async()=>{if(++reads===3)return new Promise(r=>resolve=r);return {items:[]};}});
  await x.context.views['input-datasets']();await x.context.actions['input-recovery-start']();
  const confirming=x.context.actions['input-recovery-confirm']();await new Promise(r=>setImmediate(r));
  if(change==='marker')storage.set(markerKey,'b'.repeat(64));if(change==='project')x.context.state.project='other';if(change==='account')x.context.bioflowUser={id:'v'};
  resolve({items:[]});await confirming;assert.equal(storage.size,1);assert.equal(x.posts.length,0);
 }
});
