'use strict';
assetStatus.not_checked='打开时检查文件';
const catalogueReadCache=new Map(),catalogueInflight=new Map();
const catalogueApi=api;
let catalogueEpoch=0;
api=async function(path,method='GET',body){
 if(method!=='GET'){catalogueEpoch++;catalogueReadCache.clear();return catalogueApi(path,method,body);}
 const ttl=/^\/api\/(models|skills|status)$/.test(path)?15000:/\/links\//.test(path)?10000:path==='/services/analysis/api/tasks'?4000:0;
 if(!ttl)return catalogueApi(path,method,body);
 const key=catalogueEpoch+':'+(bioflowUser?.id||'guest')+':'+path,cached=catalogueReadCache.get(key);
 if(cached&&Date.now()-cached.time<ttl)return structuredClone(cached.data);
 if(catalogueInflight.has(key))return structuredClone(await catalogueInflight.get(key));
 const promise=catalogueApi(path,method,body).then(data=>{catalogueReadCache.set(key,{time:Date.now(),data});return data;}).finally(()=>catalogueInflight.delete(key));
 catalogueInflight.set(key,promise);return structuredClone(await promise);
};
const catalogueRefresh=actions.refresh;
actions.refresh=()=>{catalogueReadCache.clear();return catalogueRefresh();};
assetCard=function(a){
 const preview=a.artifact_id&&!['empty','missing','integrity_error'].includes(a.status)?`/api/projects/${id(state.project)}/artifacts/${id(a.artifact_id)}/preview`:null;
 return `<article class="asset-card"><div class="asset-card-body"><h3 title="${esc(a.name)}">${esc(a.name)}</h3><p>${esc(a.source||'项目资料')}${a.sample_id?' · '+esc(a.sample_id):''}</p><span class="tag">${esc(assetStatus[a.status]||a.status)}</span><span class="asset-size">${a.size_bytes?uploadBytes(a.size_bytes):a.status==='empty'?'0 B':'大小待检查'}</span><div class="row">${preview?`<a class="asset-link" href="${preview}" target="_blank" rel="noopener">查看</a>`:''}${a.url?`<a class="asset-link" href="${esc(a.url)}" target="_blank" rel="noopener">下载</a>`:''}${a.status==='stored'?button('重试入库','asset-retry',`data-id="${esc(a.id)}"`):''}${a.session_id?button('打开稿件','asset-draft',`data-session="${esc(a.session_id)}"`):''}${a.document_id?button('查看文献','document',`data-id="${esc(a.document_id)}"`):''}</div></div></article>`;
};
catalogueView=async function(kind,title){
 const pid=state.project;
 const data=await api(`/api/projects/${id(pid)}/assets?kind=${id(kind)}&limit=24&offset=${state.assetPage*24}&hide_unavailable=${!!state.assetReady}`);
 const pages=Math.max(1,Math.ceil(data.total/24));
 if(state.assetPage>=pages){state.assetPage=pages-1;return catalogueView(kind,title);}
 
 const datasets=kind==='data'&&data.datasets.length?`<section class="dataset-summary"><h2>已关联的数据集</h2>${data.datasets.map(d=>`<article class="box"><h3>${esc(d.name)}</h3><p>${d.sample_count==null?'样本信息待同步':d.sample_count+' 个样本'}${d.sample_ids?.length?' · '+esc(d.sample_ids.join('、')):''}</p><p>${esc(d.reason||'点击查看样本时检查配对文件')}</p>${button('查看样本','asset-dataset',`data-id="${esc(d.dataset_id)}"`)}</article>`).join('')}</section>`:'';
 return head(title)+`<div class="row upload-entry">${button('上传列表与文件配对','upload-manager')}</div><div class="asset-toolbar"><div class="tabs">${Object.entries(assetLabels).map(([k,label])=>button(`${label} · ${data.counts[k]||0}`,'asset-kind',`data-kind="${k}" class="${kind===k?'selected':''}"`)).join('')}</div><div class="row">${button('＋ 添加文件','asset-upload')}${kind==='literature'?button('文献检索与问答','asset-literature'):''}${kind==='data'?button('双端数据上传','asset-sequencing'):''}${kind==='charts'?button('新建绘图','asset-chart-editor','class="primary"'):''}</div></div><input type="file" id="project-file-input" multiple hidden>${datasets}<div class="asset-summary"><span>已登记 ${data.counts[kind]||0} 项${data.syncing?' · 后台更新目录':''}${data.sync_error?' · 部分目录暂未更新':''}</span>${button(state.assetReady?'显示全部记录':'隐藏已知异常文件','asset-toggle')}</div><div class="asset-grid">${data.items.map(assetCard).join('')||'<div class="empty-state"><h2>暂无此类资料</h2><p>已登记资料会显示标题和摘要，具体文件在打开时读取。</p></div>'}</div><div class="asset-pagination">${state.assetPage>0?button('上一页','asset-prev'):''}<span>${state.assetPage+1} / ${pages}</span>${state.assetPage+1<pages?button('下一页','asset-next'):''}</div>`;
};
views.assets=async()=>state.assetLegacy?originalLiteratureView():await catalogueView(state.assetKind,'项目资料')+`<div class="box"><h2>样本分组与批次</h2><p>选择数据集，填写分组、批次或导入分组表。</p>${button('选择数据集','datasets')}</div>`;
