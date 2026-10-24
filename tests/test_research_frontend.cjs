const fs=require('fs'),vm=require('vm'),assert=require('assert');
const templates=JSON.parse(fs.readFileSync('runtime/chart-templates-test.json','utf8'));
const context=vm.createContext({console,setTimeout,clearTimeout,templates});
const bootstrap=`
const root={addEventListener(){}},names={},state={project:'p',assetKind:'literature'},actions={},forms={},views={assets:async()=>'<div class="row upload-entry"></div>',models:async()=>'<div class="library-summary"></div>'};
const bioflowUser={id:'owner',role:'developer'};
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const id=encodeURIComponent,pa=x=>'/api/projects/p'+x,head=x=>'<h1>'+esc(x)+'</h1>';
const button=(label,action,extra='')=>'<button data-action="'+action+'" '+extra+'>'+label+'</button>';
const uploadBytes=x=>x+' B';const $=()=>({hidden:false,click(){}});const notify=()=>{};const navigate=async()=>{};const render=async()=>{};
const chartEditor=async()=>'<form></form>';let assetCard=()=>'<article><h3>x</h3><div class="row"></div></article>';
function modelCard(){return '<span class="availability">可用</span><div class="capability-footer"></div>';}function skillCard(){return '<div class="capability-footer">可直接执行</div>';}
let uploadCount=0,lastModal;async function addUploads(){uploadCount++;}function modal(title,html){lastModal={title,html};}
async function api(path){if(path.endsWith('/figure-templates'))return {items:templates,version:'1.0'};if(path.endsWith('/figures'))return {items:[]};if(path==='/api/skills')return [];if(path.endsWith('/figure-sources'))return {items:[{id:'late-table',name:'older-results.csv',kind:'results'}]};if(path.includes('/links/task'))return [];throw Error('unexpected path '+path);}
`;
vm.runInContext(bootstrap+fs.readFileSync('frontend/research-workspace.js','utf8'),context);
(async()=>{
 const result=await vm.runInContext(`(async()=>{
   const gallery=await views.charts();
   await actions['research-chart']({dataset:{id:'pca'}});const editor=lastModal.html;
   const imports=await views.assets();state.importPurpose='results';await addUploads([{name:'report.pdf',size:12}],'p');
   return {gallery,editor,imports,confirmation:lastModal.html,uploadCount};
 })()`,context);
 assert.equal((result.gallery.match(/class="chart-type-card"/g)||[]).length,10);
 assert.equal((result.gallery.match(/src="\/static\/chart-previews\//g)||[]).length,10);
 assert(!result.gallery.includes('代码版本'));
 assert(!result.gallery.includes(templates[0].description));
 assert(result.editor.includes('older-results.csv')&&result.editor.includes('name="title"')&&result.editor.includes('name="x_label"'));
 for(const label of ['上传文献','上传测序数据与表格','导入分析结果','上传图表','上传稿件'])assert(result.imports.includes(label),label);
 assert(result.confirmation.includes('value="results" selected'));assert.equal(result.uploadCount,0,'files must await explicit classification confirmation');
 console.log('Frontend checks passed: chart gallery, editor sources, five import entries, classification before upload');
})().catch(error=>{console.error(error);process.exitCode=1;});
