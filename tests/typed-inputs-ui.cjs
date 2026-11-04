const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
test('actual typed import and task handlers with isolated owned API fixtures',{skip:!process.env.TYPED_UI_QA},async()=>{
 const {chromium}=require('playwright'),browser=await chromium.launch({channel:'msedge',headless:true});
 const source=path.resolve(__dirname,'..'),catalog={formats:{reads:['.fq'],contigs:['.fa'],mags:['.fa'],matrix:['.tsv']},read_states:['raw','clean','host_removed'],feature_types:['species','KO'],units:['fraction','counts','coverage']};
 const candidates=[{id:'asset:a',name:'s.R1.fq',size_bytes:10},{id:'asset:b',name:'s.R2.fq',size_bytes:10},{id:'artifact:m',name:'species.tsv',size_bytes:20,hints:{feature_type:'species',unit:'fraction',normalization:'none'}}];
 const registry={nodes:[{type:'contigs_input',label:'Contigs',inputs:[],outputs:[{id:'assembly',data_type:'assembly_fasta',scope:'cohort'}],parameters_schema:{}},{type:'host_removed_reads_input',label:'去宿主 reads',inputs:[],outputs:[{id:'reads',data_type:'paired_host_removed_reads',scope:'sample'}],parameters_schema:{}}]};
 const graph={nodes:[{id:'contigs',type:'contigs_input',parameters:{}},{id:'reads',type:'host_removed_reads_input',parameters:{}}],edges:[]};
 const releases={items:[{template_id:'typed',version:1,record:{name:'队列输入夹具（不计算）',workflow:graph}},{template_id:'legacy',version:1,record:{name:'旧数据清单夹具',workflow:{nodes:[{id:'input',type:'fastq_input',parameters:{}}],edges:[]}}}]};
 try{for(const width of [1500,896,390]){
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],posts=[],starts=[];let failed=false;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async r=>{const u=new URL(r.request().url()),p=u.pathname;
   if(p==='/')return r.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(source,'ai4s-interface.html'),'utf8')});
   if(p.startsWith('/static/')){const f=path.join(source,'frontend',p.slice(8));if(fs.existsSync(f))return r.fulfill({path:f});return r.fulfill({status:200,body:''});}
   let data={items:[]};
   if(p==='/api/auth/me')data={user:{id:'u',username:'fixture',role:'researcher'}};
   else if(p==='/api/projects')data=[{id:'p',name:'隔离验收项目'}];
   else if(p.endsWith('/input-datasets/candidates'))data=u.search?{items:candidates,catalog,storage:{reserve_bytes:100},has_more:false,next:null}:{items:[],catalog,has_more:true,next:{resource_offset:1,offset:0,limit:100}};
   else if(p.endsWith('/input-datasets')&&r.request().method()==='POST'){posts.push(r.request().postDataJSON());if(!failed){failed=true;return r.abort('failed');}data={id:'job',dataset_id:'job',status:'queued'};}
   else if(p.endsWith('/input-datasets/imports/job'))data={id:'job',dataset_id:'job',status:'ready'};
   else if(p.endsWith('/input-datasets'))data={items:[{dataset_id:'c',status:'ready'},{dataset_id:'r',status:'ready'}]};
   else if(/\/input-datasets\/(c|r|job)$/.test(p))data={dataset_id:p.split('/').pop(),contract:{data_type:p.endsWith('/c')?'assembly_fasta':'paired_host_removed_reads'},spec:{processing_note:'冻结的声明'},origin:{conversion:null}};
   else if(p.endsWith('/analysis/workflows/start')){starts.push(r.request().postDataJSON());return r.fulfill({status:422,contentType:'application/json',body:JSON.stringify({detail:'隔离验收：未启动计算'})});}
   else if(p.endsWith('/analysis/workflows'))data=releases;
   else if(p.endsWith('/analysis/datasets'))data={items:[{id:'legacy-data',name:'原有清单',sample_count:1}]};
   else if(p.endsWith('/workflows/registry'))data=registry;
   else if(p.endsWith('/analysis/workflows/runs/frozen'))data={run:{task_id:'frozen',status:'succeeded',input_bindings:{contigs:'old-c',reads:'old-r'}},snapshot:{workflow:graph,bound_inputs:{contigs:{record:{spec:{processing_note:'历史冻结声明'}}}},parameter_registry:registry}};
   else if(p.endsWith('/runs/frozen/artifacts'))data={items:[{artifact_id:'png',node_id:'contigs',file_name:'actual.png',artifact_type:'figure.matrix',media_type:'image/png',status:'active',downloadable:true},{artifact_id:'private',node_id:'contigs',file_name:'private.tar.gz',status:'active',downloadable:false},{artifact_id:'other',node_id:'reads',file_name:'other.tsv',status:'active',downloadable:true}]};
   else if(p.endsWith('/png/download'))return r.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')});
   else if(p==='/api/status'||p.endsWith('/history')||p.endsWith('/api/tasks')||p.endsWith('/links/task'))data=[];
   return r.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://typed-fixture.test/');await page.waitForFunction(()=>bioflowUser&&state.projects.length);
  await page.evaluate(()=>navigate('input-datasets'));
  await page.getByRole('button',{name:'加载更多来源',exact:true}).click();await page.getByText('已加载 3 个可选来源',{exact:false}).waitFor();
  await page.locator('[data-input-row=sample_id]').fill('sample1');await page.locator('[data-input-row=read1]').selectOption('asset:a');await page.locator('[data-input-row=read2]').selectOption('asset:b');
  await page.locator('[data-input-field=processing_status]').selectOption('clean');await page.locator('[data-input-field=processing_note]').fill('外部 fastp；未经平台验证');
  await page.locator('[data-form=input-import] [type=submit]').click();await page.getByRole('dialog',{name:'核对外部处理声明'}).waitFor();assert.equal(posts.length,0);await page.getByRole('button',{name:'返回修改',exact:true}).click();assert.equal(posts.length,0);
  await page.locator('[data-form=input-import] [type=submit]').click();await page.getByRole('button',{name:'确认声明并登记'}).click();await page.getByText(/网络结果不确定/).waitFor();
  await page.locator('[data-form=input-import] [type=submit]').click();await page.getByRole('button',{name:'确认声明并登记'}).click();await page.getByText('已受理；校验完成后才可用于分析。').waitFor();assert.deepEqual(posts[0],posts[1]);assert.equal(posts[0].spec.confirmed,true);
  await page.getByRole('button',{name:'刷新导入状态'}).click();await page.getByRole('heading',{name:'已完成登记'}).waitFor();
  await page.evaluate(()=>navigate('analysis'));await page.locator('[data-input-binding=contigs]').selectOption('c');await page.locator('[data-input-binding=reads]').selectOption('r');await page.locator('[name=name]').fill('隔离任务');await page.locator('[name=autoRepair]').check();
  await page.evaluate(()=>navigate('input-datasets'));assert.equal(await page.locator('[data-input-field=processing_note]').inputValue(),'外部 fastp；未经平台验证');
  await page.evaluate(()=>navigate('analysis'));assert(await page.locator('[name=autoRepair]').isChecked());
  await page.locator('[data-form=unified-run] [type=submit]').click();await page.getByRole('dialog',{name:'确认创建分析任务'}).waitFor();assert.equal(starts.length,0);await page.getByRole('button',{name:'确认提交',exact:true}).click();await page.getByText(/隔离验收：未启动计算/).waitFor();assert.equal(starts.length,1);assert.deepEqual(starts[0].input_bindings,{contigs:'c',reads:'r'});assert(!('dataset_id' in starts[0]));
  await page.evaluate(()=>actions['unified-history']({dataset:{id:'frozen'}}));await page.getByText('本次运行输入快照（只读）').click();assert.match(await page.locator('.uw-binding-evidence').innerText(),/old-c/);await page.getByRole('button',{name:'刷新节点输出'}).click();await page.locator('.uw-output-list').getByText('actual.png').waitFor();assert.equal(await page.locator('.uw-output-list a').count(),1);assert.equal(await page.locator('.uw-output-list').getByText('other.tsv').count(),0);
  await page.getByRole('button',{name:'返回新建分析',exact:true}).click();assert(!await page.locator('[name=autoRepair]').isChecked());
  await page.locator('[data-unified-template]').selectOption('legacy');await page.locator('[name=dataset_id]').selectOption('legacy-data');await page.locator('[data-form=unified-run] [type=submit]').click();await page.getByRole('button',{name:'确认提交',exact:true}).click();await page.getByText(/隔离验收：未启动计算/).waitFor();assert.deepEqual(starts[1],{name:'隔离任务',dataset_id:'legacy-data',template_id:'legacy',version:1,node_parameters:{input:{}},auto_repair:false});
  await page.evaluate(()=>navigate('input-datasets'));await page.locator('[data-input-field=kind]').selectOption('matrix');assert.equal(await page.locator('[data-input-field=unit]').inputValue(),'');await page.locator('[data-input-field=table]').selectOption('artifact:m');assert.equal(await page.locator('[data-input-field=unit]').inputValue(),'');await page.getByRole('button',{name:'采用来源建议'}).click();assert.equal(await page.locator('[data-input-field=unit]').inputValue(),'fraction');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
  if(process.env.QA_SHOTS)await page.screenshot({path:path.join(process.env.QA_SHOTS,`typed-inputs-${width}.png`),fullPage:true});
  await page.evaluate(async()=>{await actions.signout();bioflowUser={id:'different-account',username:'other'};state.projects=[{id:'p',name:'另一个账户'}];state.project='p';await navigate('input-datasets');});assert.equal(await page.locator('[data-input-field=processing_status]').inputValue(),'raw');assert.equal(await page.locator('[data-input-row=sample_id]').inputValue(),'');
  await page.close();
 }}finally{await browser.close();}
});
