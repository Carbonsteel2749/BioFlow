const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.QA_URL||'http://127.0.0.1:18880';
const shots=process.env.QA_SHOTS||'..';
const project='a87426991b5e4081ac9b1ea88f609f80';
async function enter(page){
 const failures=[];page.on('response',r=>{if(r.status()>=400)failures.push([new URL(r.url()).pathname,r.status()]);});
 await page.goto(base);
 await page.getByRole('button',{name:'登录或注册'}).click();
 await page.locator('.auth-modal [name=username]').fill(process.env.QA_USER);
 await page.locator('.auth-modal [name=password]').fill(process.env.QA_PASSWORD);
 await page.locator('.auth-modal [type=submit]').click();
 await page.locator('.auth-backdrop').waitFor({state:'hidden'});
 await page.locator('.project-list button').filter({hasText:'SRR / ERR'}).click();
 await page.locator('nav [data-page=analysis]').click();
 try{await page.locator('[data-form=wb-config]').waitFor();}
 catch(error){throw Error(error.message+'\nUI: '+await page.locator('.content').innerText()+'\nHTTP: '+JSON.stringify(failures));}
}
async function node(page,key){
 await page.locator(`[data-action=wb-node][data-node=${key}]`).click();
 await page.locator(`[data-action=wb-node][data-node=${key}][aria-current=step]`).waitFor();
}
async function tab(page,key){
 await page.locator(`[data-action=wb-tab][data-tab=${key}]`).click();
 await page.locator(`[data-action=wb-tab][data-tab=${key}][aria-selected=true]`).waitFor();
}
const live={skip:!process.env.QA_USER||!process.env.QA_PASSWORD};
test('node configuration preserves drafts, separates paper design and stays read-only',live,async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await enter(page);
  await page.locator('[data-form=wb-config] [name=name]').fill('仅浏览器草稿 · 六样本验证');
  await page.locator('[name=dataset_id]').selectOption({label:'六样本宏基因组轻量测试 · 6 个样本'});
  await page.locator('.wb-data-summary').getByText('六样本宏基因组轻量测试',{exact:true}).waitFor();
  await page.locator('[data-action=wb-groups]').click();
  await page.getByRole('heading',{name:'样本分组与批次',exact:true}).waitFor();
  assert.equal(await page.locator('.overlay .group-table tbody tr').count(),6);
  await page.locator('.overlay [data-action=close]').click();
  await node(page,'qc');
  await page.locator('[name=fastp_length_required]').fill('55');
  await node(page,'host');
  assert.match(await page.locator('.wb-panel-body').innerText(),/GRCh38/);
  await node(page,'qc');
  assert.equal(await page.locator('[name=fastp_length_required]').inputValue(),'55');
  await page.locator('.wb-runtime summary').click();
  const resources=await (await page.request.get(base+'/services/analysis/api/resources/limits')).json();
  assert.equal(await page.locator('[name=threads]').getAttribute('max'),String(resources.cpu.max_threads));
  assert.match(await page.locator('.wb-resource').innerText(),/建议剩余/);
  assert.equal(await page.locator('.wb-runtime input[type=checkbox]').isDisabled(),true);
  await page.locator('.wb-heading').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(shots,'bioflow-desktop-config.png')});
  await page.locator('nav [data-page=assets]').click();
  await page.locator('nav [data-page=analysis]').click();
  await page.locator('[name=fastp_length_required]').waitFor();
  assert.equal(await page.locator('[name=fastp_length_required]').inputValue(),'55');
  await node(page,'data');
  assert.equal(await page.locator('[data-form=wb-config] [name=name]').inputValue(),'仅浏览器草稿 · 六样本验证');
  await page.locator('[data-action=wb-review]').click();
  assert.match(await page.locator('.overlay .modal').innerText(),/不会启动计算/);
  await page.locator('.overlay [data-action=close]').click();
  await page.locator('[data-wb-template]').selectOption('paper');
  await page.getByText('论文方法设计稿 · 尚不可运行',{exact:true}).waitFor();
  assert.equal(await page.locator('[data-action=wb-review]').isDisabled(),true);
  await node(page,'catalog');
  assert.match(await page.locator('.wb-panel-body').innerText(),/MetaGeneMark/);
  await page.locator('[data-wb-template]').selectOption('standard');
  await page.locator('[data-form=wb-config] [name=name]').waitFor();
  assert.equal(await page.locator('[data-form=wb-config] [name=name]').inputValue(),'仅浏览器草稿 · 六样本验证');
  const blocked=await page.request.post(base+'/services/analysis/api/tasks',{data:{}});
  assert.equal(blocked.status(),409);
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});
test('existing task shows real node results, parameters, interpretation and logs',live,async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await enter(page);
  await page.locator('.wb-toolbar [data-action=wb-mode][data-mode=tasks]').click();
  await page.locator('.wb-task-card').filter({hasText:'六样本宏基因组轻量测试'}).getByRole('button',{name:'进度与结果'}).click();
  await page.locator('.wb-run-heading h2').waitFor();
  assert.match(await page.locator('.wb-run-heading').innerText(),/六样本宏基因组轻量测试/);
  await page.locator('.wb-figure-grid img').first().waitFor();
  await page.waitForFunction(()=>[...document.querySelectorAll('.wb-figure-grid img')].every(i=>i.complete&&i.naturalWidth>0));
  await page.locator('.wb-heading').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(shots,'bioflow-desktop-results.png')});
  await tab(page,'parameters');
  assert.match(await page.locator('.wb-definitions').innerText(),/75/);
  assert.equal(await page.locator('.wb-panel-body input').count(),0);
  await tab(page,'interpretation');
  assert.ok(await page.locator('.wb-interpretation').count()>0);
  await node(page,'functional');
  await tab(page,'results');
  const functional=await page.locator('.wb-file-list').innerText();
  assert.match(functional,/gene|ko|ec/i);
  assert.doesNotMatch(functional,/pathway|pathabundance|pathcoverage/i);
  await node(page,'pathways');
  assert.match(await page.locator('.wb-file-list').innerText(),/pathway|pathabundance|pathcoverage/i);
  await node(page,'qc');
  await tab(page,'logs');
  await page.locator('[data-action=wb-log]').first().click();
  await page.getByRole('heading',{name:'阶段日志预览'}).waitFor();
  await page.locator('.overlay [data-action=close]').click();
  assert.equal(await page.locator('.overlay').isVisible(),false);
  await page.setViewportSize({width:769,height:871});
  await page.locator('.wb-toolbar [data-action=wb-mode][data-mode=tasks]').click();
  await page.locator('.wb-heading').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(shots,'bioflow-tablet-tasks.png')});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-action=wb-mode][data-mode=configure]').click();
  await page.locator('.wb-heading').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(shots,'bioflow-mobile-config.png')});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await node(page,'taxonomy');
  await page.locator('.wb-node-heading').scrollIntoViewIfNeeded();
  assert.equal(await page.locator('[data-action=wb-node][data-node=taxonomy]').getAttribute('aria-current'),'step');
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});
test('leaving a pending task request does not poison the analysis workspace',live,async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await enter(page);
  let release,requested;
  const gate=new Promise(r=>{release=r;});
  const seen=new Promise(r=>{requested=r;});
  await page.route('**/services/analysis/api/tasks/ce68009d-7502-48b1-b545-c9c0328989a4',async route=>{requested();await gate;await route.continue();});
  await page.locator('.wb-task-card').filter({hasText:'六样本宏基因组轻量测试'}).getByRole('button',{name:'进度与结果'}).click();
  await seen;
  await page.locator('nav [data-page=assets]').click();
  await page.locator('nav [data-page=analysis]').click();
  await page.getByRole('heading',{name:'数据分析工作台',exact:true}).waitFor({timeout:5000});
  release();
  await page.waitForResponse(r=>r.url().endsWith('/api/tasks/ce68009d-7502-48b1-b545-c9c0328989a4'));
  assert.equal(await page.locator('[data-form=wb-config]').count(),1);
  assert.equal(await page.locator('.wb-run-heading').count(),0);
  assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});
test('the task results action outside analysis keeps its existing handler',live,async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  await enter(page);
  // The legacy result URL may redirect to a large archive. Never download it in QA.
  await page.route('**/api/tasks/*/results',route=>route.fulfill({json:{qa:'legacy result handler'}}));
  await page.locator('.breadcrumb-project').click();
  await page.locator('.research-record').filter({hasText:'六样本宏基因组轻量测试'}).getByRole('button',{name:'查看 ↗'}).click();
  await page.locator('.overlay [data-action=task-results]').click();
  await page.getByRole('heading',{name:'分析结果',exact:true}).waitFor({timeout:4000});
  assert.match(await page.locator('.overlay pre').innerText(),/legacy result handler/);
 }finally{await browser.close();}
});
test('a delayed log does not open over a different page',live,async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  await enter(page);
  await page.locator('.wb-task-card').filter({hasText:'六样本宏基因组轻量测试'}).getByRole('button',{name:'进度与结果'}).click();
  await page.locator('.wb-run-heading').waitFor();
  await node(page,'qc');await tab(page,'logs');
  let release,requested;
  const gate=new Promise(r=>{release=r;});const seen=new Promise(r=>{requested=r;});
  await page.route('**/api/tasks/*/log-files/*',async route=>{requested();await gate;await route.fulfill({json:{content:'read-only QA log'}});});
  await page.locator('[data-action=wb-log]').first().click();await seen;
  await page.locator('nav [data-page=assets]').click();
  const response=page.waitForResponse(r=>/\/log-files\/[^/]+$/.test(r.url()));release();await (await response).finished();
  await page.evaluate(()=>new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done))));
  assert.equal(await page.locator('.overlay').isVisible(),false);
 }finally{await browser.close();}
});
test('historical results tolerate unavailable setup and malformed optional logs',live,async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  await enter(page);
  await page.route('**/analysis/options',route=>route.fulfill({status:503,json:{detail:'QA unavailable configuration'}}));
  await page.route('**/api/tasks/*/log-files',route=>route.fulfill({body:'null',contentType:'application/json'}));
  await page.locator('.wb-task-card').filter({hasText:'六样本宏基因组轻量测试'}).getByRole('button',{name:'进度与结果'}).click();
  await page.locator('.wb-run-heading').waitFor({timeout:5000});
  assert.match(await page.locator('.wb-panel-body').innerText(),/日志目录.*格式/);
  await tab(page,'parameters');
  assert.match(await page.locator('.wb-definitions').innerText(),/taxonomy_alpha_style/);
  await node(page,'functional');
  assert.match(await page.locator('.wb-definitions').innerText(),/functional_plot_scale/);
 }finally{await browser.close();}
});
test('partial interpretation retains its warning and scientific limits',live,async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  await enter(page);
  await page.route('**/api/tasks/*/interpretation',route=>route.fulfill({json:{partial:true,display_sections:{},sections:[null,{category:'taxonomy',sample_id:'QA',text:'已完成阶段的描述性结果。'}],warnings:['功能阶段未确认成功，不采用其文件。'],limitations:['未执行组间差异检验。']}}));
  await page.locator('.wb-task-card').filter({hasText:'六样本宏基因组轻量测试'}).getByRole('button',{name:'进度与结果'}).click();
  await page.locator('.wb-run-heading').waitFor();await tab(page,'interpretation');
  const text=await page.locator('.wb-panel-body').innerText();
  assert.match(text,/阶段性解读/);assert.match(text,/功能阶段未确认成功/);assert.match(text,/未执行组间差异检验/);
 }finally{await browser.close();}
});
