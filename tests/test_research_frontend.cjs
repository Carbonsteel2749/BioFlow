const fs=require('fs'),vm=require('vm'),assert=require('assert');
const templates=JSON.parse(fs.readFileSync('runtime/chart-templates-test.json','utf8'));
const context=vm.createContext({console,setTimeout,clearTimeout,templates});
const bootstrap=`
const root={addEventListener(){}},names={},state={project:'p',assetKind:'literature'},actions={},forms={},views={assets:async()=>'<div class="row upload-entry"></div>',models:async()=>'<div class="library-summary"></div>'};
const bioflowUser={id:'owner',role:'developer'};
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const id=encodeURIComponent,pa=x=>'/api/projects/p'+x,head=x=>'<h1>'+esc(x)+'</h1>';
const button=(label,action,extra='')=>'<button data-action="'+action+'" '+extra+'>'+label+'</button>';
const previewPanel={innerHTML:''};const uploadBytes=x=>x+' B';const $=selector=>selector==='[data-research-preview]'?previewPanel:({hidden:false,click(){}});const notify=()=>{};const navigate=async()=>{};const render=async()=>{};
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
   researchFeedback('error','数值字段 count 格式错误 <bad>');
   const mapping=researchColumnInputs({id:'enrichment'},null);
   return {gallery,editor,imports,confirmation:lastModal.html,uploadCount,errorPreview:previewPanel.innerHTML,mapping};
 })()`,context);
 assert.equal((result.gallery.match(/class="chart-type-card"/g)||[]).length,templates.length);
 assert.equal((result.gallery.match(/src="\/static\/chart-previews\//g)||[]).length,templates.length);
 assert(!result.gallery.includes('代码版本'));
 assert(!result.gallery.includes(templates[0].description));
 assert(result.editor.includes('data-research-preview'));
 assert(result.errorPreview.includes('role="alert"')&&result.errorPreview.includes('count')&&result.errorPreview.includes('&lt;bad&gt;'));
 for(const key of ['term','ratio','pvalue','count'])assert(result.mapping.includes('data-column-key="'+key+'"'));
 assert(!result.mapping.includes('textarea'));
 assert(result.editor.includes('older-results.csv')&&result.editor.includes('name="title"')&&result.editor.includes('name="x_label"'));
 for(const label of ['上传文献','上传测序数据与表格','导入分析结果','上传图表','上传稿件'])assert(result.imports.includes(label),label);
 assert(result.confirmation.includes('value="results" selected'));assert.equal(result.uploadCount,0,'files must await explicit classification confirmation');
 const failedSubmit=await vm.runInContext(`(async()=>{let payload;researchState.chart='enrichment';researchState.version=null;api=async(path,method,body)=>{payload=body;throw Error('映射列不存在：GeneCount');};const values={title:'',x_label:'',y_label:'',width:'9',height:'6',font_size:'10',source_id:'source',enrichment_size_ticks:'',enrichment_bubble_min:'20',enrichment_bubble_max:'320'};const form={elements:{namedItem:k=>k in values?{value:values[k]}:null},querySelectorAll:selector=>selector==='[data-column-key]'?[{dataset:{columnKey:'count'},value:'GeneCount'}]:[]};await forms['research-figure'](form);return {payload,error:previewPanel.innerHTML};})()`,context);
 const controls=vm.runInContext('researchEnrichmentOptions({enrichment_size_ticks:[0.4,0.6,1]})',context);
 for(const key of ['enrichment_comparison','enrichment_facet','enrichment_palette','enrichment_size_ticks','enrichment_size_title'])assert(controls.includes('name="'+key+'"'));
 assert(controls.includes('0.4, 0.6, 1'));
 assert.equal(failedSubmit.payload.columns.count,'GeneCount');
 assert(!('GeneCount' in failedSubmit.payload.columns));
 assert(failedSubmit.error.includes('映射列不存在：GeneCount'));
 console.log('Frontend checks passed: chart gallery, editor sources, five import entries, classification before upload');
})().catch(error=>{console.error(error);process.exitCode=1;});
