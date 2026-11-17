const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const live={skip:!process.env.QA_BROWSER};
test('click wire exposes delete; delete and undo preserve nodes; keyboard and mobile work',live,async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{for(const viewport of [{width:1500,height:1000},{width:390,height:844}]){
  const page=await browser.newPage({viewport}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/**',route=>{const p=new URL(route.request().url()).pathname;const body=p==='/api/auth/me'?{user:null}:p.endsWith('/registry')?{nodes:[]}:p.endsWith('/datasets')||p.endsWith('/analysis/workflows')?{items:[]}:[];return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});});
  await page.goto(process.env.QA_URL||'http://127.0.0.1:18883');await page.waitForFunction(()=>window.BioFlowDesigner);
  await page.evaluate(async()=>{bioflowUser={id:'edge-qa',username:'QA',role:'developer'};state.projects=[{id:'edge-project',name:'连线测试'}];state.project='edge-project';await navigate('workflows');});
  await page.locator('[data-wd-template=metagenomics]').click();
  if(viewport.width<600)await page.locator('[data-wd-panel=canvas]').click();
  await page.locator('[data-wd-action=fit]').click();
  const paths=page.locator('.wd-edge-hit'),count=await paths.count(),nodes=await page.locator('.wd-node').count();
  const edge=await paths.first().getAttribute('data-wd-edge');
  // Use real hit testing on the curve rather than dispatching a synthetic event.
  const point=await paths.first().evaluate(p=>{const a=p.getPointAtLength(p.getTotalLength()/2),q=new DOMPoint(a.x,a.y).matrixTransform(p.getScreenCTM());return {x:q.x,y:q.y};});
  await page.mouse.click(point.x,point.y);
  await page.getByRole('button',{name:'删除连线',exact:true}).waitFor({timeout:2000});
  await page.locator('[data-wd-name]').focus();await page.keyboard.press('Delete');assert.equal(await paths.count(),count);
  await page.getByRole('button',{name:'删除连线',exact:true}).click();
  assert.equal(await paths.count(),count-1);assert.equal(await page.locator('.wd-node').count(),nodes);
  await page.locator('[data-wd-action=undo]').click();assert.equal(await paths.count(),count);
  if(viewport.width<600)await page.locator('[data-wd-panel=canvas]').click();
  await page.locator(`[data-wd-edge="${edge}"]`).dispatchEvent('click');
  await page.locator('[data-wd-action=delete-edge]').focus();await page.keyboard.press('Backspace');
  assert.equal(await paths.count(),count-1);
  const savedCount=await page.evaluate(()=>JSON.parse(localStorage.getItem(BioFlowDesigner.draftKey('edge-qa','edge-project'))).edges.length);
  assert.equal(savedCount,count-1);
  await page.locator('[data-wd-action=undo]').click();
  if(viewport.width<600)await page.locator('[data-wd-panel=canvas]').click();
  await page.locator(`[data-wd-edge="${edge}"]`).dispatchEvent('click');
  await page.locator('[data-wd-action=delete-edge]').focus();await page.keyboard.press('Delete');
  assert.equal(await paths.count(),count-1);
  await page.locator('[data-wd-action=undo]').click();assert.equal(await paths.count(),count);
  assert.deepEqual(errors,[]);console.log('EDGE_QA',viewport.width);await page.close();
 }}finally{await browser.close();}
});
