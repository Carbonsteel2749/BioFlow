const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
test('visible pending recovery protects cancellation, unavailable history, accepted-late and changed context',{skip:!process.env.TYPED_UI_QA},async()=>{
 const {chromium}=require('playwright'),browser=await chromium.launch({channel:'msedge',headless:true});
 const source=path.resolve(__dirname,'..'),key='bioflow-input-pending:'+JSON.stringify(['u','p']),digest='a'.repeat(64);
 try { for(const width of [1500,390]) {
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],posts=[],warnings=[];let unavailable=false,rows=[],reads=0;
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(['error','warning'].includes(m.type()))warnings.push(m.text());});
  await page.route('**/*',async r=>{
   const p=new URL(r.request().url()).pathname;let data={items:[]};
   if(p==='/')return r.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(source,'ai4s-interface.html'),'utf8')});
   if(p.startsWith('/static/')){const f=path.join(source,'frontend',p.slice(8));return fs.existsSync(f)?r.fulfill({path:f}):r.fulfill({body:''});}
   if(p==='/api/auth/me')data={user:{id:'u',username:'fixture',role:'researcher'}};
   else if(p==='/api/projects')data=[{id:'p',name:'隔离恢复验收项目'}];
   else if(p.endsWith('/input-datasets/candidates'))data={items:[{id:'asset:a',name:'R1.fq'},{id:'asset:b',name:'R2.fq'}],catalog:{formats:{reads:['.fq']},read_states:['raw','clean']}};
   else if(p.endsWith('/input-datasets')&&r.request().method()==='POST'){posts.push(r.request().postDataJSON());data={id:'manual',status:'queued'};}
   else if(p.endsWith('/input-datasets')){reads++;if(unavailable)return r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'历史服务暂不可用'})});data={items:rows};}
   else if(p==='/api/status'||p.endsWith('/history')||p.endsWith('/api/tasks')||p.endsWith('/links/task'))data=[];
   return r.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://typed-recovery-fixture.test/');await page.waitForFunction(()=>bioflowUser&&state.projects.length);
  const reload=async()=>{await page.evaluate(({key,digest})=>localStorage.setItem(key,digest),{key,digest});await page.reload();await page.waitForFunction(()=>bioflowUser&&state.projects.length);await page.evaluate(()=>navigate('input-datasets'));await page.getByRole('heading',{name:'导入分析数据',exact:true}).waitFor();};
  const start=()=>page.getByRole('button',{name:'核对记录并重新开始',exact:true}).click();
  const confirm=()=>page.getByRole('button',{name:'接受重复风险，解除标记',exact:true}).click();
  const stored=()=>page.evaluate(key=>localStorage.getItem(key),key);
  const submit=page.locator('[data-form=input-import] [type=submit]');
  const dialog=page.getByRole('dialog',{name:'确认重新开始导入',exact:true});
  await reload();assert.equal(await page.title(),'BioFlow · 科研工作台');assert.equal(page.url(),'http://typed-recovery-fixture.test/');
  await page.getByRole('button',{name:'刷新导入记录',exact:true}).click();await page.getByRole('button',{name:'刷新导入记录',exact:true}).click();assert.equal(await stored(),digest);assert(await submit.isDisabled());
  await start();await dialog.waitFor();assert.match(await dialog.innerText(),/不能证明先前请求未被受理/);assert.equal(await page.locator(':focus').getAttribute('data-action'),'input-recovery-cancel');
  assert.equal(await dialog.evaluate(e=>e.scrollWidth>e.clientWidth),false);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  if(process.env.QA_SHOTS)await page.screenshot({path:path.join(process.env.QA_SHOTS,`typed-recovery-dialog-${width}.png`),fullPage:false});
  await page.keyboard.press('Escape');assert.equal(await stored(),digest);assert.equal(await dialog.count(),0);
  unavailable=true;await start();await page.getByText(/记录读取失败，不能据此判定无导入/).waitFor();assert.equal(await dialog.count(),0);assert.equal(await stored(),digest);
  unavailable=false;await start();await dialog.waitFor();unavailable=true;await confirm();await page.getByText(/记录读取失败，不能据此判定无导入/).waitFor();assert.equal(await stored(),digest);assert(await submit.isDisabled());
  unavailable=false;await start();await dialog.waitFor();rows=[{id:'accepted-late',status:'validating',request_id:digest}];await confirm();await page.getByText(/已找回先前受理的导入/).waitFor();assert.equal(await stored(),null);await page.getByRole('heading',{name:'正在复制并校验',exact:true}).waitFor();assert.equal(posts.length,0);
  rows=[];await reload();await start();await dialog.waitFor();await page.evaluate(()=>{state.project='other';});await confirm();assert.equal(await stored(),digest);assert.equal(posts.length,0);
  await page.evaluate(()=>{state.project='p';});await reload();await start();await dialog.waitFor();await page.evaluate(key=>localStorage.setItem(key,'b'.repeat(64)),key);await confirm();assert.equal(await stored(),'b'.repeat(64));assert.equal(posts.length,0);
  await reload();await start();await dialog.waitFor();const before=reads;await confirm();await page.getByText(/已解除本账户、本项目的待确认标记/).waitFor();assert(reads>before);assert.equal(await stored(),null);assert.equal(posts.length,0);assert(!await submit.isDisabled());
  await page.locator('[data-input-row=sample_id]').fill('manual');await page.locator('[data-input-row=read1]').selectOption('asset:a');await page.locator('[data-input-row=read2]').selectOption('asset:b');await submit.click();await page.getByText(/已受理；校验完成后才可用于分析/).waitFor();assert.equal(posts.length,1);
  assert.deepEqual(errors,[]);assert(warnings.every(m=>/503/.test(m)),warnings.join('\n'));assert.equal(await page.locator('vite-error-overlay,nextjs-portal').count(),0);
  console.log(JSON.stringify({width,url:page.url(),title:await page.title(),historyReads:reads,manualPosts:posts.length,automaticPosts:0,pageErrors:errors,expected503ConsoleMessages:warnings.length}));await page.close();
 }}finally{await browser.close();}
});
