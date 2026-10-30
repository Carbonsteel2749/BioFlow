const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
test('live DAG progress, visible status, refresh isolation, logs and inline model design selection',{skip:!process.env.UI_QA_URL},async()=>{
 const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');const browser=await chromium.launch({channel:'msedge',headless:true});
 const source=process.env.UI_SOURCE_ROOT||path.resolve(__dirname,'..');
 const graph={schema_version:'1.0',nodes:[{id:'input',type:'fastq_input',parameters:{}},{id:'trim',type:'fastp',parameters:{threads:4}},{id:'tax',type:'taxonomy',parameters:{threads:4}},{id:'func',type:'functional_annotation',parameters:{threads:4}},{id:'report',type:'report',parameters:{}}],edges:[['input','trim'],['trim','tax'],['trim','func'],['tax','report'],['func','report']].map(([source_node,target_node],i)=>({id:'e'+i,source_node,target_node,source_port:'reads',target_port:'reads'}))};
 const release={template_id:'builtin-test',version:1,record:{name:'标准宏基因组',workflow:graph}};
 const catalog=require('../frontend/workflow-designer-model.js').catalog();
 const registry={nodes:graph.nodes.map(n=>({type:n.type,label:{input:'数据导入',trim:'质量控制',tax:'物种组成',func:'功能注释',report:'结果汇总'}[n.id],parameters_schema:n.type==='fastq_input'||n.type==='report'?{}:{threads:{type:'integer',default:4,minimum:1,maximum:64}},inputs:catalog[n.type].inputs,outputs:catalog[n.type].outputs}))};
 try{for(const width of [1500,896,390]){
  const page=await browser.newPage({viewport:{width,height:940}}),errors=[];let phase='running',detailCalls=0,releaseRead;const heldRead=new Promise(resolve=>releaseRead=resolve);
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/static/**',route=>{const p=new URL(route.request().url()).pathname.replace('/static/','');const file=path.join(source,'frontend',p);return fs.existsSync(file)?route.fulfill({path:file}):route.continue();});
  await page.route('**/api/**',async route=>{
   const p=new URL(route.request().url()).pathname;let data=[];
   if(route.request().method()!=='GET')throw Error('UI test must not mutate backend: '+p);
   if(p==='/api/auth/me')data={user:{id:'ui-qa',username:'示例用户',role:'developer',csrf:'test'}};
   else if(p==='/api/projects')data=[{id:'p1',name:'公开样本研究',description:'界面回归测试，不运行计算'}];
   else if(p.endsWith('/analysis/workflows'))data={items:[release]};
   else if(p.endsWith('/workflows/registry'))data=registry;
   else if(p.endsWith('/analysis/datasets'))data={items:[{id:'d1',name:'示例六样本',sample_count:6}]};
   else if(p.endsWith('/analysis/workflows/runs'))data={items:[{task_id:'run-a',name:'六样本分析',status:phase}],total:1};
   else if(p.endsWith('/analysis/workflows/runs/run-a')){
    detailCalls++;if(phase==='delayed')await heldRead;if(phase==='stale')return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'暂时无法读取状态'})});
    const nodes=graph.nodes.map(n=>({node_id:n.id,status:['input','trim'].includes(n.id)?'succeeded':n.id==='tax'?(phase==='failed'?'failed':'running'):'queued',progress:['input','trim'].includes(n.id)?100:n.id==='tax'?36:0,started_at:'2026-10-30T00:00:00Z',finished_at:['input','trim'].includes(n.id)?'2026-10-30T00:01:00Z':null,error_message:n.id==='tax'&&phase==='failed'?'数据库索引缺失':null}));
    data={run:{task_id:'run-a',name:'六样本分析',status:phase,started_at:'2026-10-30T00:00:00Z',nodes},snapshot:{workflow:graph,parameter_registry:registry,version:1}};
   }
   else if(p.endsWith('/runs/run-a/logs'))data={text:'[taxonomy] 数据库索引缺失\n这是测试日志，不是实际任务日志'};
   else if(p==='/api/models')data={items:[{id:'m14',name:'qwen3:14b',category:'language',provider:'ollama',available:true},{id:'m7',name:'qwen:7b',category:'language',provider:'ollama',available:true},{id:'embed',name:'embedding-test',category:'embedding',available:true}]};
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto(process.env.UI_QA_URL);await page.waitForFunction(()=>typeof state!=='undefined'&&state.projects?.length);
  if(process.env.UI_SOURCE_ROOT){
   // The live page may not yet include the new local motion helper during RED/GREEN.
   if(!await page.evaluate(()=>!!window.BioFlowMotion)&&fs.existsSync(path.join(source,'frontend/analysis-motion.js')))await page.addScriptTag({path:path.join(source,'frontend/vendor/gsap-3.14.2.min.js')}).then(()=>page.addScriptTag({path:path.join(source,'frontend/analysis-motion.js')}));
  }
  await page.evaluate(()=>navigate('analysis'));
  await page.locator('[data-action=unified-history]').click();
  await page.locator('[data-uw-live]').waitFor({timeout:4000});
  assert.match(await page.locator('[data-uw-summary]').innerText(),/运行中/);
  assert.match(await page.locator('[data-uw-summary]').innerText(),/估算/);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'no page-level horizontal overflow');
  if(process.env.QA_SHOTS)await page.screenshot({path:path.join(process.env.QA_SHOTS,'analysis-running-'+width+'.png'),fullPage:false});
  assert.equal(await page.locator('[data-uw-flow]').count(),1);assert.equal(await page.locator('[data-uw-node=tax]').getAttribute('data-depth'),await page.locator('[data-uw-node=func]').getAttribute('data-depth'));
  assert.equal(await page.evaluate(()=>window.gsap?.version),'3.14.2');assert.equal(await page.evaluate(()=>window.BioFlowMotion.activeCount()),1,'the running dependency uses a real GSAP tween');
  await page.locator('[data-action=unified-node][data-node=tax]').click();
  assert.match(await page.locator('[data-uw-node-run]').innerText(),/36%/);
  if(process.env.QA_SHOTS){await page.locator('[data-uw-track]').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(process.env.QA_SHOTS,'analysis-stage-'+width+'.png'),fullPage:false});}
  phase='failed';await page.locator('[data-action=unified-live-refresh]').click();
  await page.locator('[data-uw-node-run]').getByText('数据库索引缺失',{exact:false}).waitFor();assert.equal(await page.locator('[data-uw-flow]').count(),0);
  assert.equal(await page.evaluate(()=>window.BioFlowMotion.activeCount()),0,'failure stops owned animation');
  await page.getByRole('button',{name:'查看运行日志',exact:true}).click();await page.getByRole('dialog',{name:'流程运行日志'}).getByText(/这是测试日志/).waitFor();
  await page.getByRole('button',{name:'关闭日志',exact:true}).click();
  phase='running';await page.locator('[data-action=unified-live-refresh]').click();await page.locator('[data-uw-flow]').waitFor();
  await page.locator('[data-uw-node=tax]').focus();const before=detailCalls,refreshes=Number(await page.locator('[data-uw-live]').getAttribute('data-refreshes'));await page.waitForFunction(n=>Number(document.querySelector('[data-uw-live]')?.dataset.refreshes)>n,refreshes,{timeout:8000});assert(detailCalls>before);
  assert.equal(await page.evaluate(()=>document.activeElement?.dataset.uwNode),'tax','refresh must preserve keyboard focus in the stage track');
  phase='stale';await page.locator('[data-action=unified-live-refresh]').click();await page.locator('[data-uw-live-error]').getByText(/暂时无法读取/).waitFor();assert.equal(await page.locator('[data-uw-flow]').count(),0);
  await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.evaluate(()=>window.BioFlowMotion?.activeCount()||0),0);
  if(process.env.QA_SHOTS)await page.screenshot({path:path.join(process.env.QA_SHOTS,'analysis-live-'+width+'.png'),fullPage:false});
  await page.getByRole('button',{name:'设计流程',exact:true}).click();await page.locator('[data-wd-template=metagenomics]').click();
  const inline=page.locator('[data-type=repair_llm] [data-wd-inline-model]');await inline.waitFor();await inline.selectOption('m7');
  await page.locator('[data-type=repair_llm] .wd-node-title').click();
  assert.equal(await page.locator('[data-wd-param=model_id]').inputValue(),'m7');assert.match(await page.locator('[data-type=repair_llm]').innerText(),/设计/);assert.equal(await inline.locator('option[value=embed]').count(),0);
  await page.locator('[data-wd-param=model_id]').selectOption('m14');assert.equal(await inline.inputValue(),'m14');
  await page.locator('[data-wd-action=undo]').click();assert.equal(await page.locator('[data-type=repair_llm] [data-wd-inline-model]').inputValue(),'m7');
  const canvasTab=page.locator('[data-wd-panel=canvas]');if(await canvasTab.isVisible())await canvasTab.click();
  const interpret=page.locator('[data-type=interpret_llm] [data-wd-inline-model]');await interpret.scrollIntoViewIfNeeded();await interpret.selectOption('m14');assert.equal(await page.locator('[data-type=repair_llm] [data-wd-inline-model]').inputValue(),'m7');
  if(process.env.QA_SHOTS)await page.screenshot({path:path.join(process.env.QA_SHOTS,'analysis-designer-'+width+'.png'),fullPage:false});
  await page.reload();await page.waitForFunction(()=>state.projects?.length);await page.evaluate(()=>navigate('workflows'));await page.locator('[data-wd-action=resume]').click();
  assert.equal(await page.locator('[data-type=repair_llm] [data-wd-inline-model]').inputValue(),'m7');assert.equal(await page.locator('[data-type=interpret_llm] [data-wd-inline-model]').inputValue(),'m14');
  phase='running';await page.evaluate(()=>navigate('analysis'));await page.locator('[data-action=unified-history]').click();
  phase='delayed';await page.locator('[data-action=unified-live-refresh]').click();await page.evaluate(()=>navigate('home'));releaseRead();await page.waitForTimeout(250);assert.equal(await page.locator('[data-uw-live]').count(),0);assert.equal(await page.evaluate(()=>window.BioFlowMotion?.activeCount()||0),0);assert.deepEqual(errors,[]);
  await page.close();
 }}finally{await browser.close();}
});
