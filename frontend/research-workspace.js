'use strict';
names['personal-library']='个人文献库';
const importNames={literature:'文献',data:'测序数据与表格',results:'分析结果',charts:'图表',drafts:'稿件'};
const researchState={category:'全部',chart:'',version:null,preview:null,imports:[],pid:''};
const positions=[['best','自动'],['upper right','右上'],['upper left','左上'],['lower right','右下'],['lower left','左下'],['outside_right','图外右侧'],['outside_bottom','图外下方']];
actions['home-model']=async()=>{const data=await api('/api/models');const model=data.items.find(m=>m.id==='writing-model');modal('选择模型',`<div class="model-option"><b>Qwen3-14B</b><span class="tag">${model?.enabled===0?'已停用':model?.available===true?'当前模型 · 可用':model?.available===false?'服务未就绪':'等待服务确认'}</span></div><p>当前研究指令使用 Qwen3-14B。模型服务和用途由开发者管理。</p>`);};
const opt=(values,value)=>values.map(([k,v])=>`<option value="${esc(k)}" ${k===value?'selected':''}>${esc(v)}</option>`).join('');
function suggestPurpose(file){const n=file.name.toLowerCase();if(/\.(fastq|fq)(\.gz)?$/.test(n))return 'data';if(/\.(svg|png|jpe?g|tiff?)$/.test(n))return 'charts';if(/\.(docx?|md)$/.test(n))return 'drafts';if(/\.(pdf|bib|ris|enw)$/.test(n))return 'literature';return 'data';}
const addConfirmedUploads=addUploads;
addUploads=async function(files,pid){
 researchState.imports=files;researchState.pid=pid;
 const purpose=state.importPurpose||'auto';state.importPurpose='auto';
 const taskIds=await api(`/api/projects/${id(pid)}/links/task`).catch(()=>[]);
 modal('确认文件用途',`<p>确认保存位置后开始上传；文件格式相同也可以有不同用途。</p><form data-form="confirm-imports"><div class="import-confirm-list">${files.map((file,i)=>`<label class="import-confirm-row"><span>${esc(file.name)}<small>${uploadBytes(file.size)}</small></span><select name="purpose-${i}" aria-label="${esc(file.name)}用途">${opt(Object.entries(importNames),/\.(fastq|fq)(\.gz)?$/i.test(file.name)?'data':purpose==='auto'?suggestPurpose(file):purpose)}</select></label>`).join('')}</div><details><summary>分析结果来源（可选）</summary><label>结果类型<input name="result_type" maxlength="120" placeholder="如差异分析、GO 富集"></label><label>来源软件<input name="software" maxlength="120" placeholder="如 DESeq2、外部分析平台"></label><label>关联本项目任务<select name="task_id">${opt([['','暂不关联'],...taskIds.map(t=>[t.external_id||t,typeof t==='string'?t.slice(0,12):t.name||t.external_id])],'')}</select></label></details><button class="primary">确认用途并上传</button></form>`);
};
forms['confirm-imports']=async form=>{
 const files=researchState.imports,pid=researchState.pid;
 const selections=files.map((file,i)=>({file,purpose:form.elements.namedItem('purpose-'+i).value}));
 for(const item of selections)if(/\.(fastq|fq)(\.gz)?$/i.test(item.file.name)&&item.purpose!=='data')throw Error('FASTQ 文件请归入测序数据');
 const details=Object.fromEntries(['result_type','software','task_id'].map(k=>[k,form.elements.namedItem(k).value]));
 $('.overlay').hidden=true;
 for(const item of selections)await addConfirmedUploads([item.file],pid,{purpose:item.purpose,details:item.purpose==='results'?details:{}});
 researchState.imports=[];
};
actions['import-purpose']=el=>{state.importPurpose=el.dataset.kind;$('#project-file-input').click();};
actions['asset-upload']=()=>{state.importPurpose=state.assetKind||'auto';$('#project-file-input').click();};
actions['personal-library']=()=>navigate('personal-library');
const assetsBeforeManagement=views.assets;
views.assets=async()=>{
 const html=await assetsBeforeManagement();if(state.assetLegacy)return html;
 const controls=`<div class="research-import-bar">${Object.entries(importNames).map(([key,label])=>button((key==='results'?'导入':'上传')+label,'import-purpose',`data-kind="${key}"`)).join('')}${button('个人文献库','personal-library')}</div>`;
 return html.replace('<div class="row upload-entry">',controls+'<div class="row upload-entry">');
};
const assetCardBeforeManagement=assetCard;
assetCard=function(asset){
 let html=assetCardBeforeManagement(asset);const m=asset.metadata||{};if(asset.kind==='literature'&&asset.status==='failed')html=html.replace('<div class="row">',`<div class="row">${button('重试索引','asset-retry',`data-id="${esc(asset.id)}"`)}`);
 if(asset.kind==='literature')html=html.replace('</h3>',`</h3><p>${esc(m.title||'')}</p><p class="asset-origin">文件：已保存 · 知识库：${esc(({ready:'已索引',indexed:'已索引',completed:'已索引',failed:'索引失败',processing:'正在索引',pending:'等待索引'})[m.index_status]||m.index_status||'等待索引')}</p>`);
 if(asset.kind==='charts'&&m.origin==='external_import')html=html.replace('</h3>','</h3><small class="asset-origin">归档图表；提供源表后可生成参数编辑版本。</small>');if(m.origin==='external_import')html=html.replace('</h3>',`</h3><small class="asset-origin">外部导入${m.software?' · '+esc(m.software):''}${m.result_type?' · '+esc(m.result_type):''}</small>`);
 if(asset.figure_id)html=html.replace('<div class="row">',`<div class="row">${button('编辑与预览','research-version',`data-id="${esc(asset.figure_id)}"`)}`);
 return html;
};
views['personal-library']=async()=>{
 const data=await api('/api/library/literature');state.personalPapers=data.items;
 return head('个人文献库')+`<p>文献由当前账户保存，可关联多个项目；每个项目分别显示知识库索引状态。</p><div class="row">${button('上传文献到当前项目','paper-upload')}${button('返回项目资料','assets')}</div><div class="capability-grid">${data.items.map(p=>`<article class="box"><span class="tag">文件已保存</span><h2>${esc(p.metadata.title||p.name)}</h2><p>${esc([p.metadata.authors,p.metadata.year,p.metadata.doi].filter(Boolean).join(' · '))}</p><p>${esc(p.metadata.tags||'')}</p><p>${p.projects.map(q=>esc(q.name)+'：'+esc(({ready:'已索引',indexed:'已索引',completed:'已索引',processing:'正在索引'})[q.index_status]||q.index_status)).join('<br>')||'尚未关联项目'}</p><div class="row">${button('编辑条目','paper-edit',`data-id="${esc(p.id)}"`)}${button('关联当前项目','paper-link',`data-id="${esc(p.id)}"`)}${p.projects.some(q=>q.id===state.project)?button('解除当前项目关联','paper-unlink',`data-id="${esc(p.id)}"`):''}${p.projects.filter(q=>q.id===state.project&&['stored','failed'].includes(q.status)).map(q=>button('重试当前项目索引','asset-retry',`data-id="${esc(q.asset_id)}"`)).join('')}<a href="/api/library/literature/${id(p.id)}/download">下载原文</a></div></article>`).join('')||'<div class="empty-state">上传第一篇文献后，会在这里显示。</div>'}</div>`;
};
actions['paper-upload']=async()=>{await navigate('assets');state.importPurpose='literature';$('#project-file-input').click();};
actions['paper-edit']=el=>{const p=state.personalPapers.find(x=>x.id===el.dataset.id);state.editPaper=p.id;modal('编辑文献条目',`<form data-form="paper-details">${[['title','题名'],['authors','作者'],['year','年份'],['doi','DOI'],['tags','标签']].map(([k,label])=>`<label>${label}<input name="${k}" value="${esc(p.metadata[k]||'')}" ${k==='title'?'required':''}></label>`).join('')}<button class="primary">保存条目</button></form>`);};
forms['paper-details']=async f=>{await api('/api/library/literature/'+id(state.editPaper),'PATCH',Object.fromEntries(new FormData(f)));$('.overlay').hidden=true;await render();};
actions['paper-link']=async el=>{await api(pa('/literature/'+id(el.dataset.id)+'/link'),'POST',{});notify('已关联当前项目，知识库索引状态按项目分别显示。');await render();};

const originalChartWorkspace=chartEditor;
async function legacyChartWorkspace(){
 let html=await originalChartWorkspace();
 html=html.replace('生成图表版本</button>',`生成图表版本</button><p>已有版本可点击“编辑显示参数”修改标题、轴名和图例。</p>`);
 html=html.replace(/(<button data-action="figure-files" data-job="([^"]+)"[^>]*>.*?<\/button>)/g,(_,buttonHtml,job)=>buttonHtml+button('编辑显示参数','legacy-display',`data-job="${esc(job)}"`));
 return button('返回图表分类','research-gallery')+html;
}
const chartIcons={stacked:'▥',shannon:'⌁',pca:'⁙',volcano:'⋀',heatmap:'▦',enrichment:'⠿',gsea:'⌁',roc:'◜',ppi:'⌘',venn:'◉',go:'▥',sankey:'≋'};
views.charts=async()=>{
 if(state.researchLegacy)return legacyChartWorkspace();
 const [templates,versions,abilities]=await Promise.all([api(pa('/figure-templates')),api(pa('/figures')),api('/api/skills')]);
 researchState.templates=templates.items;researchState.versions=versions.items;
 const categories=['全部',...new Set(templates.items.map(c=>c.category))];
 const visible=templates.items.filter(c=>researchState.category==='全部'||c.category===researchState.category);
 return head('图表工作台')+`<div class="research-chart-intro"><div><p>选择图型，关联结果表，预览并保存独立版本。</p></div><div class="row">${button('已有项目图表','research-existing')}${button('宏基因组绘图','research-legacy')}</div></div><div class="category-pills">${categories.map(c=>button(esc(c),'research-category',`data-category="${esc(c)}" class="${c===researchState.category?'selected':''}"`)).join('')}</div><div class="research-chart-grid">${visible.map(c=>{const disabled=abilities.find(a=>a.name==='chart:'+c.id)?.enabled===0;return `<button class="chart-type-card" data-action="research-chart" data-id="${c.id}" ${disabled?'disabled':''} aria-label="${esc(c.name)}${disabled?'（已停用）':''}"><img src="/static/chart-previews/${c.id}.png" alt="${esc(c.name)}模拟数据示例" loading="lazy" width="600" height="400"><h2>${esc(c.name)}${disabled?' · 已停用':''}</h2></button>`;}).join('')}</div><h2>预览与保存记录</h2><div class="research-versions">${versions.items.map(v=>`<article class="box"><div><span class="tag">${v.saved?'已保存':'预览'} · ${esc(({queued:'排队中',running:'生成中',completed:'已完成',failed:'失败'})[v.status])}</span><h3>${esc(v.parameters.title||templates.items.find(c=>c.id===v.chart)?.name||v.chart)}</h3><small>${esc(v.created_at)} · ${esc(v.source.name)}</small>${v.error?`<p role="alert">${esc(v.error)}</p>`:''}</div>${button(v.status==='completed'?'打开与编辑':'查看状态','research-version',`data-id="${v.id}"`)}</article>`).join('')||'<p>还没有绘图记录。</p>'}</div>`;
};
actions['research-category']=async el=>{researchState.category=el.dataset.category;await render();};
actions['research-legacy']=async()=>{state.researchLegacy=true;await navigate('charts');};
actions['research-gallery']=async()=>{state.researchLegacy=false;await navigate('charts');};
actions['research-existing']=async()=>{state.assetKind='charts';state.assetPage=0;await navigate('assets');};
actions['asset-chart-editor']=actions['research-gallery'];
const taskChartsBeforeGallery=actions['task-charts'];actions['task-charts']=el=>{state.researchLegacy=true;return taskChartsBeforeGallery(el);};
actions['research-chart']=async el=>{researchState.version=null;researchState.chart=el.dataset.id;await openResearchEditor();};
const researchInputFields={pca:[['sample','样本编号'],['group','分组（可选）'],['label','点标注（可选）']],stacked:[['sample','样本编号'],['group','分组（可选）'],['taxon','菌群分类'],['abundance','丰度']],shannon:[['depth','测序深度'],['group','分组（可选）'],['shannon','Shannon 均值'],['lower','区间下界'],['upper','区间上界']],heatmap:[['sample','样本编号']],volcano:[['gene','基因名称'],['log2fc','变化倍数 log2FC'],['pvalue','P 值或校正 P 值']],enrichment:[['term','富集条目'],['ratio','GeneRatio 富集比例'],['pvalue','P 值或校正 P 值'],['count','基因数量'],['direction','分面组别（可选，如 activated/suppressed）']],gsea:[['position','排序位置'],['es','运行富集分数'],['hit','命中标记（可选）']],roc:[['label','真实标签 0/1'],['score','预测分数'],['model','模型名称（可选）']],ppi:[['source','来源蛋白'],['target','目标蛋白'],['weight','边权重（可选）']],venn:[['set','集合名称'],['member','成员编号']],go:[['term','GO 条目'],['count','数量'],['ontology','BP/CC/MF（可选）']],sankey:[['source','来源节点'],['target','目标节点'],['value','流量数值']]};
function researchColumnInputs(template,version){return `<details><summary>列名对应：填写你上传表中的列名</summary><p>左侧是程序字段，不能修改；右侧填写你的表头。留空使用同名列。</p><div class="research-column-heading"><span>程序需要的字段</span><span>上传表中的列名</span></div>${researchInputFields[template.id].map(([key,label])=>`<label class="research-column-row"><span><b>${esc(key)}</b><small>${esc(label)}</small></span><input data-column-key="${key}" value="${esc(version?.columns?.[key]||'')}" placeholder="${esc(key)}" maxlength="200" list="research-source-columns" ${version?.status==='completed'?'disabled':''} aria-label="${esc(key)} 对应的上传表列名"></label>`).join('')}<datalist id="research-source-columns"></datalist>${['pca','heatmap'].includes(template.id)?'<p>其余列为数值特征，保留原表列名；至少两列，勿放入文字备注列。</p>':''}${version?.status==='completed'?'<p>该版本使用固定快照和列对应，显示参数修改沿用原映射。</p>':''}</details>`;}
function researchFeedback(kind,message){const panel=$('[data-research-preview]');if(!panel)return;panel.innerHTML=`<div class="research-preview-message ${kind}" role="${kind==='error'?'alert':'status'}"><h3>${kind==='error'?'图表未能生成':kind==='busy'?'正在生成图表':'图表预览'}</h3><p>${esc(message)}</p>${kind==='error'?'<p>请检查左侧列名对应、所选数据字段及数值格式，修改后重新生成。</p>':''}</div>`;}
function researchEnrichmentOptions(p){return `<fieldset><legend>对照与分面</legend><label>比较标题<input name="enrichment_comparison" maxlength="120" value="${esc(p.enrichment_comparison||'')}" placeholder="例如 AB vs. SPF"></label><label>分面方式<select name="enrichment_facet">${opt([['none','不分面'],['direction','按 direction 分面（如 activated / suppressed）']],p.enrichment_facet||'none')}</select></label><small>分面使用表中的真实 direction 列；比较标题是显示文字。</small></fieldset><fieldset><legend>颜色与色条</legend><label>常用色系<select name="enrichment_palette">${opt([['paper','红—紫'],['RdYlBu_r','红黄蓝 · RdYlBu'],['viridis','蓝绿黄 · Viridis'],['YlGnBu','黄绿蓝 · YlGnBu'],['magma','紫红黄 · Magma'],['cividis','蓝黄 · Cividis']],p.enrichment_palette||'paper')}</select></label><label>颜色对应的数值<select name="enrichment_color">${opt([['pvalue','原始 P 值'],['neg_log10','−log10(P 值)']],p.enrichment_color||'pvalue')}</select></label></fieldset><fieldset><legend>气泡大小图例</legend><label>图例标题<input name="enrichment_size_title" maxlength="100" value="${esc(p.enrichment_size_title||'')}" placeholder="留空按所选字段生成 GeneRatio 或 Count"></label><label>图例数值（逗号分隔，最多 8 个）<input name="enrichment_size_ticks" value="${esc(p.enrichment_size_ticks?.join(', ')||'')}" placeholder="如 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1"></label><small>留空自动生成；填写真实数值，图例圆点大小由同一数据映射计算。</small><label>气泡基础面积（pt²）<input type="number" name="enrichment_bubble_min" min="1" max="800" value="${p.enrichment_bubble_min??20}" required></label><label>气泡最大面积（pt²）<input type="number" name="enrichment_bubble_max" min="10" max="1600" value="${p.enrichment_bubble_max??320}" required></label><p>右侧上方显示大小图例，下方显示色条；每个数值只显示一次，共用上方图例标题。</p></fieldset>`;}
function researchCompositionOptions(chart,p){if(!['pca','stacked','shannon'].includes(chart))return '';return `<fieldset><legend>图型参数</legend><label>分类色系<select name="series_palette">${opt([['tab20','Tab20'],['Set2','Set2'],['Dark2','Dark2'],['Paired','Paired']],p.series_palette||(chart==='shannon'?'Dark2':'tab20'))}</select></label>${chart==='pca'?`<label><input type="checkbox" name="pca_log1p" ${p.pca_log1p?'checked':''}> 先做 log1p 转换（非负数值）</label><label><input type="checkbox" name="pca_show_labels" ${p.pca_show_labels?'checked':''}> 显示 label 或样本编号</label><label>散点面积（pt²）<input type="number" name="pca_point_size" min="5" max="250" value="${p.pca_point_size??45}" required></label>`:chart==='stacked'?`<label>柱子表示<select name="stacked_level">${opt([['group','按 group 展示样本等权均值'],['sample','每个 sample 一根柱子']],p.stacked_level||'group')}</select></label><label>保留丰度最高的分类数<input name="stacked_top_n" type="number" min="1" max="20" value="${p.stacked_top_n??10}" required></label><label><input name="stacked_normalize" type="checkbox" ${p.stacked_normalize!==false?'checked':''}> 每个样本归一化为 100%</label><small>其余分类合并为 Other taxa；先按样本归一化，再计算组均值。</small>`:`<label><input name="shannon_show_band" type="checkbox" ${p.shannon_show_band!==false?'checked':''}> 显示 lower / upper 阴影区间</label><label>阴影透明度<input name="shannon_band_alpha" type="number" min="0" max="0.6" step="0.05" value="${p.shannon_band_alpha??.18}" required></label><label>线宽<input name="shannon_line_width" type="number" min="0.5" max="6" step="0.5" value="${p.shannon_line_width??2}" required></label><label><input name="shannon_log_depth" type="checkbox" ${p.shannon_log_depth?'checked':''}> 测序深度使用对数坐标</label><label><input name="shannon_show_points" type="checkbox" ${p.shannon_show_points?'checked':''}> 显示深度点</label><small>直接使用表中提供的区间，区间统计方法见数据说明。</small>`}</fieldset>`;}
async function openResearchEditor(){
 const template=(researchState.templates||[]).find(c=>c.id===researchState.chart);if(!template)throw Error('请从图表分类中选择图型');
 const version=researchState.version;const sources=(await api(pa('/figure-sources'))).items;
 const p=version?.parameters||{},defaults=version?.result?.defaults||{};
 const legend=version?.result?.legend||[];const ordered=[...(p.legend_order||[]),...legend.map(s=>s.id).filter(k=>!(p.legend_order||[]).includes(k))];
 modal(template.name+' · 编辑与预览',`<div class="research-editor"><p>${esc(template.description)}</p><p>输入列：${esc(template.columns)} · <a href="${pa('/figure-templates/'+id(template.id)+'/example')}">下载示例表</a></p><form data-form="research-figure">${version?.status==='completed'?`<p>复用版本 ${esc(version.id.slice(0,8))} 的数据快照；原文件保持不变。</p>`:`<label>来源结果表<select name="source_id" required><option value="">请选择项目内 CSV / TSV 表格</option>${opt(sources.map(a=>[a.id,a.name+' · '+(a.source||'项目上传')+(a.sample_id?' · '+a.sample_id:'')]),version?.source?.id||'')}</select></label>`}${researchColumnInputs(template,version)}<fieldset><legend>显示设置</legend><label>图标题<input name="title" maxlength="300" value="${esc(p.title??'')}" placeholder="留空自动生成"></label><div class="research-common-size">${[['width','画布宽度（英寸）',['enrichment','stacked','shannon'].includes(template.id)?12:9,4,20],['height','画布高度（英寸）',template.id==='enrichment'?8:6,3,16],['font_size','字号（pt）',10,6,24]].map(([k,label,v,min,max])=>`<label>${label}<input type="number" name="${k}" min="${min}" max="${max}" step="0.5" value="${p[k]??v}" required></label>`).join('')}</div></fieldset><fieldset><legend>数据映射</legend>${template.id==='enrichment'?`<label>横轴使用的数据<select name="enrichment_x">${opt([['ratio','GeneRatio · 富集比例'],['count','Count · 基因数量']],p.enrichment_x||'ratio')}</select></label><label>气泡大小使用的数据<select name="enrichment_size">${opt([['count','Count · 基因数量'],['ratio','GeneRatio · 富集比例']],p.enrichment_size||'count')}</select></label><p>选择后重新计算坐标与气泡面积；纵轴为 term，颜色可选 P 值或 −log10(P)，大小图例在下方设置。</p>`:`<p>${esc(({stacked:'横轴为样本或分组，纵轴为丰度，各颜色对应 taxon。',shannon:'横轴 depth，纵轴 shannon，阴影范围为 lower 至 upper。',pca:'横轴 PC1，纵轴 PC2，由输入矩阵计算。',volcano:'横轴 log2fc，纵轴 −log10(pvalue)。',heatmap:'行对应样本，列对应特征，按矩阵值着色。',gsea:'横轴 position，纵轴 es。',roc:'横轴假阳性率，纵轴真阳性率，由 label 和 score 计算。',ppi:'source 与 target 构成网络，坐标由布局计算。',venn:'set 与 member 决定集合及交集数量。',go:'条目和计数来自 term 与 count。',sankey:'source、target、value 决定流向及带宽。'})[template.id]||'按图型输入数据计算。')}</p>`}</fieldset><details><summary>坐标轴标题文字（仅改文字，不改变数据）</summary>${[['x_label','横轴标题文字'],['y_label','纵轴标题文字']].map(([k,label])=>`<label>${label}<input name="${k}" maxlength="200" value="${esc(p[k]??'')}" placeholder="留空根据数据映射自动生成"></label>`).join('')}</details>${template.id==='pca'?`<label><input type="checkbox" name="standardize" ${p.standardize?'checked':''}> 特征标准化（所有 PCA 默认先中心化）</label>`:''}${researchCompositionOptions(template.id,p)}${template.id==='enrichment'?researchEnrichmentOptions(p):legend.length?`<fieldset><legend>图例显示</legend>${ordered.map(key=>{const s=legend.find(x=>x.id===key);return s?`<label>${esc(s.label)}<input data-series-id="${esc(s.id)}" value="${esc(p.legend_labels?.[s.id]||s.label)}" maxlength="200"></label>`:'';}).join('')}<label>图例顺序<select name="legend_order" multiple size="${Math.min(legend.length,6)}">${ordered.map(key=>`<option value="${esc(key)}">${esc(legend.find(s=>s.id===key)?.label||key)}</option>`).join('')}</select><small>选中图例项后使用下方按钮调整，未选择时保持顺序。</small></label><div class="row">${button('上移','research-legend-up')}${button('下移','research-legend-down')}</div><label>图例位置<select name="legend_position">${opt(positions,p.legend_position||(['stacked','shannon'].includes(template.id)?'outside_right':'best'))}</select></label></fieldset>`:`<label>图例位置<select name="legend_position">${opt(positions,p.legend_position||(['stacked','shannon'].includes(template.id)?'outside_right':'best'))}</select></label><p class="muted">首次预览后可编辑分类或大小图例的名称和顺序。</p>`}<button class="primary">${version?.status==='completed'?'更新预览（生成新版本）':'生成预览'}</button><p class="research-render-state" aria-live="polite"></p></form>${version?.status==='completed'?`<aside class="research-preview" data-research-preview aria-live="polite"><img src="${pa('/figures/'+id(version.id)+'/file/png')}" alt="图表预览"><div class="row">${button(version.saved?'已保存到项目图表':'保存到项目图表','research-save',`data-id="${version.id}" ${version.saved?'disabled':''}`)}${['svg','pdf','png'].map(ext=>`<a href="${pa('/figures/'+id(version.id)+'/file/'+ext+'?download=true')}">导出 ${ext.toUpperCase()}</a>`).join('')}</div></aside>`:`<aside class="research-preview" data-research-preview aria-live="polite"><div class="research-preview-message"><h3>图表预览</h3><p>设置数据与参数后，点击生成预览。</p></div></aside>`}</div>`);
 if(version?.status==='failed')researchFeedback('error',version.error||'预览生成失败');
}
for(const [action,delta] of [['research-legend-up',-1],['research-legend-down',1]])actions[action]=()=>{const select=$('[name="legend_order"]'),selected=[...select.selectedOptions];for(const option of delta<0?selected:selected.reverse()){const sibling=delta<0?option.previousElementSibling:option.nextElementSibling;if(sibling){if(delta<0)select.insertBefore(option,sibling);else select.insertBefore(sibling,option);}}};
forms['research-figure']=async form=>{
 try{
 const old=researchState.version;const parameters={};
 for(const k of ['title','x_label','y_label'])parameters[k]=form.elements.namedItem(k).value||null;
 for(const k of ['width','height','font_size'])parameters[k]=Number(form.elements.namedItem(k).value);
 for(const k of ['enrichment_x','enrichment_size','enrichment_comparison','enrichment_facet','enrichment_palette','enrichment_color','enrichment_size_title'])if(form.elements.namedItem(k))parameters[k]=form.elements.namedItem(k).value;
 if(researchState.chart==='enrichment'){
  for(const k of ['enrichment_bubble_min','enrichment_bubble_max'])parameters[k]=Number(form.elements.namedItem(k).value);
  const raw=form.elements.namedItem('enrichment_size_ticks').value.trim();
  parameters.enrichment_size_ticks=raw?raw.split(/[,，\s]+/).filter(Boolean).map(Number):null;
  if(parameters.enrichment_size_ticks?.some(v=>!Number.isFinite(v)))throw Error('大小图例刻度请填写数字，用逗号分隔。');
 }
 for(const k of ['series_palette','stacked_level'])if(form.elements.namedItem(k))parameters[k]=form.elements.namedItem(k).value;
 for(const k of ['pca_point_size','stacked_top_n','shannon_band_alpha','shannon_line_width'])if(form.elements.namedItem(k))parameters[k]=Number(form.elements.namedItem(k).value);
 for(const k of ['pca_log1p','pca_show_labels','stacked_normalize','shannon_show_band','shannon_log_depth','shannon_show_points'])if(form.elements.namedItem(k))parameters[k]=form.elements.namedItem(k).checked;
 parameters.standardize=form.elements.namedItem('standardize')?.checked||false;parameters.legend_labels=Object.fromEntries([...form.querySelectorAll('[data-series-id]')].map(e=>[e.dataset.seriesId,e.value]));
 if(researchState.chart==='enrichment'&&old&&(parameters.enrichment_size!==(old.parameters.enrichment_size||'count'))){parameters.legend_labels={};}
 parameters.legend_order=[...(form.elements.namedItem('legend_order')?.options||[])].map(o=>o.value);parameters.legend_position=form.elements.namedItem('legend_position')?.value||'best';if(researchState.chart==='enrichment'&&old&&parameters.enrichment_size!==(old.parameters.enrichment_size||'count')){parameters.legend_order=[];parameters.legend_labels={};}
 if(researchState.chart==='enrichment'){parameters.legend_labels={};parameters.legend_order=[];parameters.legend_position='outside_right';}
 const columns=Object.fromEntries([...form.querySelectorAll('[data-column-key]')].filter(e=>e.value.trim()).map(e=>[e.dataset.columnKey,e.value.trim()]));
 researchFeedback('busy','正在检查输入并绘图，请稍候…');
 const row=await api(pa('/figures'),'POST',{chart:researchState.chart,source_id:form.elements.namedItem('source_id')?.value||'',parent_id:old?.status==='completed'?old.id:'',columns,parameters});
 const target=form.querySelector('.research-render-state');target.textContent='正在生成预览，可关闭窗口后在绘图记录中查看。';
 await pollResearchFigure(row.id,target,state.project);
 }catch(error){researchFeedback('error',error.message||'生成请求失败');}
};
async function pollResearchFigure(fid,target,pid){
 for(let count=0;count<90;count++){
  if(state.project!==pid||$('.overlay').hidden||!target.isConnected)return;
  const row=await api(`/api/projects/${id(pid)}/figures/${id(fid)}`);
  if(row.status==='completed'){researchState.version=row;await openResearchEditor();return;}
  if(row.status==='failed'){if(researchState.version?.status!=='completed')researchState.version=row;researchFeedback('error',row.error||'预览生成失败');target.textContent='请修正输入后重试。';return;}
  await new Promise(resolve=>setTimeout(resolve,1500));
 }
 target.textContent='绘图仍在处理，请稍后从绘图记录打开查看。';researchFeedback('busy',target.textContent);
}
actions['research-version']=async el=>{
 const row=await api(pa('/figures/'+id(el.dataset.id)));if(!['completed','failed'].includes(row.status)){notify('当前状态：'+row.status);await render();return;}
 if(!researchState.templates)researchState.templates=(await api(pa('/figure-templates'))).items;
 researchState.chart=row.chart;researchState.version=row;await openResearchEditor();
};
actions['research-save']=async el=>{await api(pa('/figures/'+id(el.dataset.id)+'/save'),'POST',{});researchState.version.saved=1;await openResearchEditor();notify('已保存到项目图表，原版本保持不变。');};

const modelsBeforeManagement=views.models;
views.models=async()=>{
 const html=await modelsBeforeManagement();return html.replace('<div class="library-summary">','<p class="ability-hint">模型提供推理或检索服务；Skills 执行具体研究任务。可用状态来自实际服务。</p><div class="library-summary">');
};
const modelCardBeforeManagement=modelCard;
modelCard=function(model){let html=modelCardBeforeManagement(model);if(!model.enabled&&model.enabled!==undefined)html=html.replace(/<span class="availability[^>]*>.*?<\/span>/,'<span class="availability unavailable">已停用</span>');if(bioflowUser.role==='developer')html=html.replace('<div class="capability-footer">',`<div class="capability-footer">${button('管理','ability-manage',`data-kind="models" data-id="${esc(model.id)}"`)}`);return html;};
const skillCardBeforeManagement=skillCard;
skillCard=function(skill){let html=skill.chart_id?`<section class="capability-card skill-card" data-search="${esc((skill.display_name||skill.name)+' '+skill.description)}"><span class="tag">绘图能力 · v${esc(skill.version)}</span><h2>${esc(skill.display_name||researchState.templates?.find(c=>c.id===skill.chart_id)?.name||skill.name)}</h2><p>${esc(skill.description)}</p><p>输入：CSV / TSV 结果表<br>输出：SVG、PDF、PNG</p><div class="capability-footer">${button(skill.enabled===0?'已停用':'进入图表工作台','ability-chart',`data-chart="${esc(skill.chart_id)}" ${skill.enabled===0?'disabled':''}`)}</div></section>`:skillCardBeforeManagement(skill);if(skill.enabled===0)html=html.replace('可直接执行','已停用').replace('data-action="skill"','disabled data-action="skill"');if(bioflowUser.role==='developer')html=html.replace('<div class="capability-footer">',`<div class="capability-footer">${button('管理','ability-manage',`data-kind="skills" data-id="${esc(skill.name)}"`)}`);return html;};
actions['ability-chart']=async el=>{if(el.dataset.chart==='legacy')return actions['research-legacy']();state.researchLegacy=false;await navigate('charts');researchState.chart=el.dataset.chart;researchState.version=null;await openResearchEditor();};
actions['ability-manage']=async el=>{const kind=el.dataset.kind;const entries=kind==='models'?(await api('/api/models')).items:await api('/api/skills');const item=entries.find(x=>(x.id||x.name)===el.dataset.id);state.editAbility={kind,id:el.dataset.id};modal('管理'+(kind==='models'?'模型':'能力'),`<form data-form="ability-details"><p>${esc(item.name)} · ${esc(item.version||item.provider||'服务配置')}</p><label><input name="enabled" type="checkbox" ${item.enabled===0?'':'checked'}> 启用</label><label>功能分类<input name="category" value="${esc(item.category||'')}" maxlength="80"></label><label>用途说明<textarea name="description" maxlength="1000">${esc(item.description||'')}</textarea></label><p>启用状态控制后续调用；已有成果仍可查看和下载。</p><button class="primary">保存管理设置</button></form>`);};
forms['ability-details']=async f=>{await api('/api/library/abilities/'+state.editAbility.kind+'/'+id(state.editAbility.id),'PATCH',{enabled:f.elements.namedItem('enabled').checked,category:f.elements.namedItem('category').value,description:f.elements.namedItem('description').value});$('.overlay').hidden=true;await render();};
actions['legacy-display']=async el=>{
 const job=await service('analysis',`/api/tasks/${id(state.task)}/figures/jobs/${id(el.dataset.job)}`);state.legacyEdit=job;
 if(job.status!=='succeeded')throw Error('请等待原图生成成功后再编辑');
 const display=job.result.display?.[0]||{},p=job.request.parameters||{},defaults=display.defaults||{};const legacyOrder=[...(p.legend_order||[]),...(display.legend||[]).map(x=>x.id).filter(k=>!(p.legend_order||[]).includes(k))];
 modal('编辑图表显示参数',`<form data-form="legacy-display"><p>复用原图的数据来源和样本分组，生成独立的重绘版本。</p>${[['title','标题'],['x_label','横轴名称'],['y_label','纵轴名称']].map(([k,label])=>`<label>${label}<input name="${k}" value="${esc(p[k]??defaults[k]??'')}"></label>`).join('')}${(display.legend||[]).map(s=>`<label>${esc(s.label)}<input data-legacy-series="${esc(s.id)}" value="${esc(p.legend_labels?.[s.id]||s.label)}" maxlength="200"></label>`).join('')}${legacyOrder.length?`<label>图例顺序<select name="legend_order" multiple>${legacyOrder.map(k=>`<option value="${esc(k)}">${esc(display.legend.find(x=>x.id===k)?.label||k)}</option>`).join('')}</select></label><div class="row">${button('上移','research-legend-up')}${button('下移','research-legend-down')}</div>`:''}<label>图例位置<select name="legend_position">${opt(positions,p.legend_position||'best')}</select></label><button class="primary">生成新版本</button></form>`);
};
forms['legacy-display']=async f=>{
 const old=state.legacyEdit.request,p={...old.parameters};for(const k of ['title','x_label','y_label'])p[k]=f.elements.namedItem(k).value;
 p.legend_position=f.elements.namedItem('legend_position').value;p.legend_order=[...(f.elements.namedItem('legend_order')?.options||[])].map(x=>x.value);p.legend_labels=Object.fromEntries([...f.querySelectorAll('[data-legacy-series]')].map(e=>[e.dataset.legacySeries,e.value]));
 await service('analysis',`/api/tasks/${id(state.task)}/figures/jobs`,'POST',{data_type:old.data_type,chart:old.chart,source_artifact_ids:old.source_artifact_ids,sample_ids:old.sample_ids,groups:old.groups,parameters:p});
 $('.overlay').hidden=true;await render();notify('已提交新绘图版本，分析任务不会重跑。');
};

actions['paper-unlink']=async el=>{await api(pa('/literature/'+id(el.dataset.id)+'/unlink'),'POST',{});notify('已解除当前项目关联，个人文献原文保留。');await render();};

actions['project-edit']=el=>{const p=state.projects.find(p=>p.id===el.dataset.id);if(!p)return;modal('编辑项目',`<form data-form="project-edit" data-project-id="${esc(p.id)}"><label>项目名称<input name="name" required maxlength="120" value="${esc(p.name)}"></label><label>项目简介<textarea name="description" maxlength="2000">${esc(p.description)}</textarea></label><button class="primary">保存修改</button></form>`);};
forms['project-edit']=async f=>{await api('/api/projects/'+id(f.dataset.projectId),'PATCH',Object.fromEntries(new FormData(f)));$('.overlay').hidden=true;await loadProjects();};
actions['project-delete']=el=>{const p=state.projects.find(p=>p.id===el.dataset.id);if(!p)return;modal('删除项目',`<p>确定删除项目“${esc(p.name)}”？删除后不再显示在项目列表，原始文件及分析结果保留。</p>${button('确认删除','project-delete-confirm',`data-id="${esc(p.id)}"`)}${button('取消','close')}`);};
actions['project-delete-confirm']=async el=>{await api('/api/projects/'+id(el.dataset.id),'DELETE');$('.overlay').hidden=true;if(state.project===el.dataset.id){state.project='';state.task='';state.session='';state.chapter='';state.page='home';}await loadProjects();localStorage.setItem('bioflow.project',state.project);};

root.addEventListener('change',event=>{if(event.target.name==='enrichment_x'){const form=event.target.closest('form');if(form)form.elements.namedItem('x_label').value='';}});

root.addEventListener('change',async event=>{if(event.target.name!=='source_id'||!event.target.value)return;const input=event.target;try{const data=await api(pa('/figure-sources/'+id(input.value)));if(!input.isConnected)return;const list=$('#research-source-columns');if(list)list.innerHTML=data.columns.map(c=>`<option value="${esc(c)}"></option>`).join('');}catch(error){researchFeedback('error','读取表头失败：'+error.message);}});

root.addEventListener('change',event=>{if(event.target.name==='enrichment_size'){const f=event.target.form;if(f?.elements.namedItem('enrichment_size_ticks'))f.elements.namedItem('enrichment_size_ticks').value='';}});
