const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const enabled=!!process.env.QA_URL;
test('task submission uses visible review, displays preflight failure and prevents duplicate requests',{skip:!enabled},async()=>{
 const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{for(const width of [1500,390]){
  const page=await browser.newPage({viewport:{width,height:940}}),errors=[],native=[];let requests=0,body,mode='error';
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',async d=>{native.push(d.type());await d.dismiss();});
  if(process.env.QA_SOURCE_ROOT)await page.route('**/static/unified-workflows.js*',r=>r.fulfill({path:path.join(process.env.QA_SOURCE_ROOT,'frontend/unified-workflows.js')}));
  if(process.env.QA_SOURCE_ROOT)await page.route('**/static/analysis-workbench.css*',r=>r.fulfill({path:path.join(process.env.QA_SOURCE_ROOT,'frontend/analysis-workbench.css')}));
  // Never create a real compute task during browser regression.
  await page.route('**/api/projects/*/analysis/workflows/start',async route=>{
   requests++;body=route.request().postDataJSON();await new Promise(r=>setTimeout(r,200));
   if(mode==='network')return route.abort('failed');
   await route.fulfill({status:mode==='error'?422:201,contentType:'application/json',body:JSON.stringify(mode==='error'?{detail:'MAG 数据库尚未就绪，请补齐分类与功能数据库'}:{task_id:'fake-ui-no-compute'})});
  });
  await page.route('**/analysis/workflows/runs/fake-ui-no-compute',r=>r.fulfill({status:200,contentType:'application/json',body:JSON.stringify({run:{task_id:'fake-ui-no-compute',status:'queued'},snapshot:{workflow:{nodes:[{id:'input',type:'fastq_input',parameters:{}}],edges:[]},parameter_registry:{nodes:[]}}})}));
  await page.goto(process.env.QA_URL);await page.locator('[data-action=account]').click();
  await page.locator('[data-auth-form] [name=username]').fill(process.env.QA_USER||'developer');
  await page.locator('[data-auth-form] [name=password]').fill(process.env.QA_PASSWORD||'developer');
  await page.locator('[data-auth-form] button[type=submit]').click();
  await page.waitForFunction(()=>typeof bioflowUser!=='undefined'&&bioflowUser&&state.projects?.length);
  await page.evaluate(async()=>{state.project=state.projects.find(p=>p.name.includes('SRR'))?.id||state.projects[0].id;await navigate('analysis');});
  const select=page.locator('[data-unified-template]');await select.waitFor();
  const option=await select.locator('option').evaluateAll(es=>es.find(e=>e.textContent.includes('MAG'))?.value);
  assert(option,'MAG template should be available');await select.selectOption(option);
  const form=page.locator('[data-form=unified-run]');await form.locator('[name=name]').fill('提交反馈回归 · 不执行计算');
  const dataset=await form.locator('select[name=dataset_id],select[name=dataset]').locator('option').evaluateAll(es=>es.find(e=>e.value)?.value);assert(dataset);
  await form.locator('select[name=dataset_id],select[name=dataset]').selectOption(dataset);
  await form.locator('button[type=submit]').click();
  console.log('Submission observation',JSON.stringify({nativeDialogs:native,requests,pageErrors:errors,notice:await page.locator('.notice').textContent(),invalid:await form.locator(':invalid').evaluateAll(es=>es.map(e=>({name:e.name,value:e.value,message:e.validationMessage}))),handler:await page.evaluate(()=>typeof forms['unified-run'])}));
  const dialog=page.getByRole('dialog',{name:'确认创建分析任务'});
  await dialog.waitFor({timeout:3000});assert.equal(requests,0);assert.deepEqual(native,[]);
  assert.match(await dialog.innerText(),/提交反馈回归/);
  await dialog.getByRole('button',{name:'返回修改',exact:true}).click();assert.equal(requests,0);
  await form.locator('button[type=submit]').click();await dialog.waitFor();
  await dialog.getByRole('button',{name:'确认提交',exact:true}).click();
  await page.evaluate(()=>actions['unified-submit-confirm']());
  const feedback=form.locator('[data-unified-submit-status]');await feedback.getByText(/MAG 数据库尚未就绪/).waitFor();
  assert.equal(requests,1);assert.equal(body.dataset_id,dataset);assert.equal(body.auto_repair,false);
  assert.equal(await form.locator('button[type=submit]').isEnabled(),true);
  assert.deepEqual(errors,[]);assert.deepEqual(native,[]);
  if(process.env.QA_SHOTS)await page.screenshot({path:path.join(process.env.QA_SHOTS,`submit-feedback-${width}.png`),fullPage:false});
  mode='success';await form.locator('button[type=submit]').click();await dialog.waitFor();await dialog.getByRole('button',{name:'确认提交',exact:true}).click();
  await page.getByText('历史流程 · 固定运行快照',{exact:false}).waitFor();assert.equal(requests,2);
  await page.getByRole('button',{name:'返回新建分析',exact:true}).click();
  const newForm=page.locator('[data-form=unified-run]');assert(await newForm.locator('button[type=submit]').isEnabled());
  mode='network';await newForm.locator('button[type=submit]').click();await dialog.waitFor();await dialog.getByRole('button',{name:'确认提交',exact:true}).click();
  await newForm.locator('[data-unified-submit-status]').getByText(/提交结果尚未确认/).waitFor();assert(await newForm.locator('button[type=submit]').isDisabled());assert.equal(requests,3);
  assert.deepEqual(errors,[]);
  await page.close();
 }}finally{await browser.close();}
});
