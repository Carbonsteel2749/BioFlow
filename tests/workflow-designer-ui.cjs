const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const live={skip:!process.env.QA_BROWSER},base=process.env.QA_URL||'http://127.0.0.1:18880';
async function enter(page){
 // External API is controlled here; real components, event handlers and persistence are exercised.
 await page.route('**/api/**',async route=>{const url=new URL(route.request().url());let body=[];
  if(url.pathname==='/api/auth/me')body={user:null};
  if(url.pathname==='/api/models')body=[{id:'writing-model',name:'qwen3:14b',category:'language',provider:'ollama',available:true,configured:true},{id:'emb',name:'embedding',category:'embedding',provider:'ollama'}];
  if(url.pathname.endsWith('/workflows/registry'))body={registry_version:1,nodes:[]};
  if(url.pathname.endsWith('/datasets'))body={items:[{id:'dataset1',name:'六样本公开测试',sample_count:6,status:'available'}]};
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto(base);await page.waitForFunction(()=>typeof wbHeader==='function');
 await page.evaluate(()=>{bioflowUser={id:'qa-user',username:'QA',role:'developer'};state.projects=[{id:'qa-project',name:'公开样本设计验证'}];state.project='qa-project';state.page='analysis';chrome();document.querySelector('.content').innerHTML=wbHeader(wbState());});
 await page.getByRole('button',{name:'设计 Workflow',exact:true}).click();await page.getByRole('heading',{name:'设计你的分析流程',exact:true}).waitFor();
}
async function selectNode(page,type){await page.locator(`.wd-node[data-type="${type}"] .wd-node-title`).first().click();}
test('canvas template, independent parameters, policy, undo, drag, save, export and no compute',live,async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1580,height:1000}}),errors=[],writes=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET'&&!r.url().includes('/api/auth/'))writes.push(r.url());});
  await enter(page);await page.locator('[data-wd-template=metagenomics]').click();await page.locator('.wd-viewport').waitFor();
  await selectNode(page,'fastp');await page.locator('[data-wd-param=length_required]').fill('77');await page.locator('[data-wd-param=length_required]').blur();
  await page.locator('[data-wd-add=fastp]').click();assert.equal(await page.locator('[data-wd-param=length_required]').inputValue(),'50');
  await page.locator('.wd-node[data-type=fastp] .wd-node-title').first().click();assert.equal(await page.locator('[data-wd-param=length_required]').inputValue(),'77');
  const moveNode=page.locator('.wd-node[data-type=fastp]').first(),before=await moveNode.getAttribute('style');
  const head=await moveNode.locator('.wd-node-title').boundingBox();await page.mouse.move(head.x+20,head.y+15);await page.mouse.down();await page.mouse.move(head.x+55,head.y+50,{steps:6});await page.mouse.up();
  assert.notEqual(await moveNode.getAttribute('style'),before);
  await page.locator('[data-wd-action=undo]').click();assert.equal(await moveNode.getAttribute('style'),before);
  await selectNode(page,'repair_llm');assert.equal(await page.locator('[data-wd-param=enabled]').isChecked(),false);
  await page.locator('[data-wd-param=enabled]').check();await page.locator('[data-wd-param=model_id]').selectOption({label:'qwen3:14b · ollama'});
  await selectNode(page,'interpret_llm');assert.equal(await page.locator('[data-wd-param=model_id]').inputValue(),'');
  await page.locator('[data-wd-action=validate]').click();assert.match(await page.locator('.wd-dialog').innerText(),/不启动计算/);await page.getByRole('button',{name:'关闭提示',exact:true}).click();
  const dl=page.waitForEvent('download');await page.locator('[data-wd-action=export]').click();assert.match((await dl).suggestedFilename(),/\.json$/);
  await page.locator('[data-wd-action=save]').click();await page.reload();await page.waitForFunction(()=>typeof wbHeader==='function');await page.evaluate(()=>{bioflowUser={id:'qa-user',username:'QA',role:'developer'};state.projects=[{id:'qa-project',name:'公开样本设计验证'}];state.project='qa-project';navigate('workflows');});
  await page.locator('[data-wd-action=resume]').click();await selectNode(page,'fastp');assert.equal(await page.locator('[data-wd-param=length_required]').inputValue(),'77');
  await page.screenshot({path:path.join(process.env.QA_SHOTS||'..','workflow-designer-desktop.png')});
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
 }finally{await browser.close();}
});
test('typed ports block FastQC-to-fastp and custom parameters remain design-only',live,async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1580,height:1000}});await enter(page);await page.locator('[data-wd-template=metagenomics]').click();
  await page.locator('.wd-node[data-type=fastqc] [data-wd-out=report]').click();await page.locator('.wd-node[data-type=fastp] [data-wd-in=reads]').first().click();
  assert.match(await page.locator('.wd-status').innerText(),/不兼容/);
  await selectNode(page,'fastp');await page.locator('.wd-custom summary').click();await page.locator('[data-wd-action=add-custom]').click();
  await page.locator('[data-custom-field=name]').fill('user_limit');await page.locator('[data-custom-field=value]').fill('12');
  await page.locator('[data-wd-action=validate]').click();assert.match(await page.locator('.wd-dialog').innerText(),/尚未映射/);
 }finally{await browser.close();}
});
test('mobile switches panels, 16S remains design-only and imports reject corrupt data',live,async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}});await enter(page);await page.locator('[data-wd-template=amplicon16s]').click();
  await page.locator('[data-wd-panel=nodes]').click();await page.locator('.wd-outline [data-wd-select]').filter({hasText:'去噪与 ASV'}).click();
  await page.locator('[data-wd-param=trunc_len_f]').fill('180');await page.locator('[data-wd-param=trunc_len_f]').blur();
  assert.match(await page.locator('.wd-inspector').innerText(),/待接入|尚未连接执行后端/);
  await page.locator('[data-wd-action=import]').click();await page.locator('[data-wd-file]').setInputFiles({name:'bad.json',mimeType:'application/json',buffer:Buffer.from('{bad')});
  await page.locator('.wd-status').filter({hasText:'JSON'}).waitFor();assert.equal(await page.locator('[data-wd-param=trunc_len_f]').inputValue(),'180');
  const over=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2);assert.equal(over,false);
  await page.screenshot({path:path.join(process.env.QA_SHOTS||'..','workflow-designer-mobile.png')});
 }finally{await browser.close();}
});
