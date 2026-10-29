const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
test('registered MAG library exposes editable resources and readiness warning',{skip:!process.env.QA_BROWSER},async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{for(const viewport of [{width:1500,height:1000},{width:390,height:844}]){
  const page=await browser.newPage({viewport});
  await page.route('**/api/**',route=>{const p=new URL(route.request().url()).pathname;let body=[];
   if(p==='/api/auth/me')body={user:null};
   if(p.endsWith('/registry'))body={nodes:[{type:'assembly',label:'联合组装',category:'mag',inputs:[{id:'reads',accepted_data_types:['paired_host_removed_reads'],accepted_scopes:['sample'],required:true,multiple:false,collect:true}],outputs:[{id:'assembly',data_type:'assembly_fasta',scope:'cohort'}],parameters_schema:{threads:{type:'integer',default:8,title:'计算线程'},memory_gb:{type:'integer',default:32,title:'内存'}},tool_requirements:['MEGAHIT'],database_requirements:[]}]};
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
  });
  await page.goto(process.env.QA_URL||'http://127.0.0.1:18883');await page.waitForFunction(()=>window.BioFlowDesigner);
  await page.evaluate(async()=>{bioflowUser={id:'mag-qa',role:'developer',username:'QA'};state.projects=[{id:'p',name:'MAG 节点测试'}];state.project='p';await navigate('workflows');});
  await page.locator('[data-wd-template=blank]').click();if(viewport.width<600)await page.locator('[data-wd-panel=nodes]').click();
  await page.locator('.wd-category').filter({has:page.locator('summary', {hasText:/^MAG$/})}).locator('summary').click();
  await page.locator('[data-wd-add=assembly]').click();
  assert.equal(await page.locator('[data-wd-param=threads]').inputValue(),'8');assert.equal(await page.locator('[data-wd-param=memory_gb]').inputValue(),'32');
  assert.match(await page.locator('.wd-inspector').innerText(),/运行预检|资源预检/);assert.match(await page.locator('.wd-inspector').innerText(),/联合组装输出属于队列/);
  await page.locator('[data-wd-param=memory_gb]').fill('16');await page.locator('[data-wd-param=memory_gb]').blur();
  await page.screenshot({path:`D:/4组数据分析/.codex_remote_stage/mag-nodes-${viewport.width}.png`,fullPage:true});await page.close();
 }}finally{await browser.close();}
});
