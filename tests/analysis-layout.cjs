const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
test('analysis task cards remain readable at the reported 769px viewport',{skip:!process.env.QA_USER||!process.env.QA_PASSWORD},async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try {
  const page=await browser.newPage({viewport:{width:769,height:871}});
  await page.goto(process.env.QA_URL||'http://127.0.0.1:18880/');
  await page.getByRole('button',{name:'登录或注册'}).click();
  await page.locator('.auth-modal [name=username]').fill(process.env.QA_USER);
  await page.locator('.auth-modal [name=password]').fill(process.env.QA_PASSWORD);
  await page.locator('.auth-modal form button[type=submit], .auth-modal form button.primary').click();
  await page.locator('.auth-backdrop').waitFor({state:'hidden'});
  await page.locator('.project-list button').filter({hasText:'SRR / ERR'}).click();
  await page.locator('nav [data-page=analysis]').click();
  await page.getByText('六样本宏基因组轻量测试',{exact:true}).first().waitFor();
  const name=page.getByText('六样本宏基因组轻量测试',{exact:true}).first();
  const box=await name.boundingBox();
  await page.screenshot({path:process.env.QA_SCREENSHOT||'../analysis-layout.png',fullPage:false});
  assert.ok(box.width>180,`task title has only ${box.width}px of width`);
  assert.ok(box.height<100,`task title has ${box.height}px of height`);
 } finally {await browser.close();}
});
