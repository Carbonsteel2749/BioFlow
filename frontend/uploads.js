'use strict';
names.uploads='上传与配对';
const uploadJobs=new Map();
let uploadActive=0,uploadRenderQueued=false,uploadSelectFor='',uploadPollTimer;
const uploadStatus={preparing:'等待文件校验',open:'等待继续',paused:'已暂停',cancelled:'已取消',finalizing:'正在校验与入库',completed:'已完成'};
const uploadBytes=n=>{if(n<1024)return n+' B';for(const unit of ['KB','MB','GB','TB']){n/=1024;if(n<1024||unit==='TB')return n.toFixed(n<10?1:0)+' '+unit;}};
const uploadPath=(pid,tail='')=>`/api/projects/${id(pid)}/uploads${tail}`;
const activeUploadUser=()=>bioflowUser?.id||'';
function uploadJobKey(pid,uid){return activeUploadUser()+':'+pid+':'+uid;}
function uploadRefresh(){
 if(uploadRenderQueued||state.page!=='uploads')return;
 uploadRenderQueued=true;setTimeout(()=>{uploadRenderQueued=false;if(state.page!=='uploads')return;for(const row of state.uploadRows||[]){const job=uploadJobs.get(uploadJobKey(state.project,row.id));const target=$(`[data-upload-row="${row.id}"]`);if(target)target.outerHTML=uploadRow(job?.row||row);}},120);
}
function uploadRow(row){
 const job=uploadJobs.get(uploadJobKey(state.project,row.id));
 const phase=job?.phase||row.status;let bytes=job?.bytes??row.received_bytes;
 const hashing=phase==='hashing',percent=Math.min(100,Math.floor((hashing?(job?.hashed||0):bytes)/row.size_bytes*100));
 const caption=({hashing:'正在校验本地文件',queued:'等待上传',uploading:'正在上传',finalizing:'正在校验与入库',offline:'传输中断，分片已保留',error:'需要处理'})[phase]||uploadStatus[row.status]||row.status;
 const busy=(job?.running||phase==='queued')&&['hashing','uploading','queued'].includes(phase);
 const finalizing=phase==='finalizing'||row.status==='finalizing';
 const speed=phase==='uploading'&&job.speed>0?uploadBytes(job.speed)+'/s':'';
 const eta=speed&&row.size_bytes>bytes?`约剩 ${Math.ceil((row.size_bytes-bytes)/job.speed)} 秒`:'';
 return `<article class="upload-file" data-upload-row="${esc(row.id)}"><div class="row upload-file-heading"><b>${esc(row.name)}</b><span>${caption}</span></div><div class="upload-progress" role="progressbar" aria-label="${esc(row.name)}${hashing?'校验':'上传'}进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}"><span style="width:${percent}%"></span></div><div class="row upload-file-meta"><span>${hashing?'校验':'已传'} ${uploadBytes(hashing?(job.hashed||0):bytes)} / ${uploadBytes(row.size_bytes)} · ${percent}%</span><span>${speed} ${eta}</span></div>${job?.error||row.error?`<p class="upload-error" role="alert">${esc(job?.error||row.error)}</p>`:''}<div class="row upload-file-actions">${busy?button('暂停','upload-pause',`data-id="${esc(row.id)}"`):''}${!busy&&!finalizing&&row.status!=='completed'?button(job?.file?'继续上传':'选择原文件继续','upload-resume',`data-id="${esc(row.id)}"`):''}${!finalizing&&!['completed','cancelled'].includes(row.status)?button('取消上传','upload-cancel',`data-id="${esc(row.id)}"`):''}${row.status==='completed'?'<span class="upload-success">文件已归入项目资料</span>':''}</div>${!job?.file&&!['completed','cancelled','finalizing'].includes(row.status)?'<p class="muted">刷新后请选择原文件继续，已完成的分片会保留。</p>':''}</article>`;
}
function guessReadPairs(files){
 const groups=new Map();let n=0;
 for(const file of files){
  const m=file.name.match(/^(.+?)[_.-](?:R)?([12])(?:[_.-]001)?\.(?:fastq|fq)(?:\.gz)?$/i);
  const key=m?m[1]:file.id;if(!groups.has(key))groups.set(key,{sample:/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(key)?key:'sample_'+(++n),r1:[],r2:[]});
  const group=groups.get(key);group[m?.[2]==='2'?'r2':'r1'].push(file);
 }
 return [...groups.values()];
}
function pairForm(data){
 const used=new Set(data.pairs.flatMap(p=>[p.read1,p.read2]));const files=data.files.filter(f=>!used.has(f.id));
 let groups=guessReadPairs(files);
 const saved=state.uploadPairDrafts?.[state.project];
 if(saved)groups=saved.map(r=>({sample:r.sample,r1:files.filter(f=>f.id===r.read1),r2:files.filter(f=>f.id===r.read2)}));
 state.uploadCandidates=files;
 if(!files.length)return '<div class="box"><h2>R1 / R2 文件配对</h2><p>上传并完成校验的 FASTQ 文件会在这里整理，同一样本的双端文件确认后建立数据集。</p>'+button('更新配对文件','upload-pair-refresh')+'</div>';
 const options=(values,selected)=>`<option value="">选择文件</option>${values.map(f=>`<option value="${esc(f.id)}" ${f.id===selected?'selected':''}>${esc(f.name)}</option>`).join('')}`;
 return `<form data-form="upload-pairs" class="box upload-pair-form"><div class="row"><h2>R1 / R2 文件配对</h2><div class="row">${button('更新配对文件','upload-pair-refresh')}${button('＋ 增加一行','upload-pair-add')}</div></div><p>同一样本的文件会自动放在一行。请核对编号与双端文件，再确认建立数据集。</p><div class="upload-pair-rows">${groups.map(g=>`<div class="upload-pair-row"><label>样本编号<input name="sample" value="${esc(g.sample)}" required pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,127}"></label><label>Read 1<select name="read1" required>${options(files,g.r1.length===1?g.r1[0].id:'')}</select></label><label>Read 2<select name="read2" required>${options(files,g.r2.length===1?g.r2[0].id:'')}</select></label>${button('移除此行','upload-pair-remove')}${g.r1.length>1||g.r2.length>1?'<p class="upload-error">存在多个候选文件，请手动选择。</p>':!g.r1.length||!g.r2.length?'<p class="muted">这一行缺少双端文件，请选择对应文件或补传。</p>':''}</div>`).join('')}</div><label>数据集名称<input name="dataset_name" value="${esc(current().name)}" required maxlength="120"></label><button class="primary">确认配对并建立数据集</button><div id="upload-pairs-result" aria-live="polite"></div></form>`;
}
views.uploads=async()=>{
 const pid=state.project;const data=await api(uploadPath(pid));
 clearTimeout(uploadPollTimer);
 if(data.items.some(r=>r.status==='finalizing'))uploadPollTimer=setTimeout(()=>refreshUploadList(pid).catch(()=>{}),2500);
 state.uploadCandidates=data.files;state.uploadRows=data.items;
 return head('上传与文件配对')+`<div class="row upload-toolbar">${button('＋ 添加文件','upload-add','class="primary"')}${button('返回项目资料','upload-assets')}${button(state.showCancelledUploads?'隐藏已取消':'显示已取消','upload-cancelled')}</div><input type="file" id="queue-file-input" multiple hidden><input type="file" id="queue-reselect-input" hidden><div class="upload-list">${data.items.filter(r=>state.showCancelledUploads||r.status!=='cancelled').map(uploadRow).join('')||'<div class="empty-state"><h2>还没有上传记录</h2><p>添加文献、数据表或 FASTQ 文件后，在这里查看进度。</p></div>'}</div><div id="upload-pair-area">${pairForm(data)}</div>${data.pairs.length?`<section class="box"><h2>已建立的配对</h2>${data.pairs.map(p=>`<p>${esc(p.sample_id)} · 双端文件已确认</p>`).join('')}${button('选用这组数据进入分析','upload-analysis',`data-dataset="${esc(data.pairs[data.pairs.length-1].dataset_id)}"`)}</section>`:''}`;
};
async function refreshUploadList(pid){
 if(state.page!=='uploads'||state.project!==pid)return;
 const data=await api(uploadPath(pid));if(state.page!=='uploads'||state.project!==pid)return;
 state.uploadRows=data.items;
 for(const row of data.items){const job=uploadJobs.get(uploadJobKey(pid,row.id));if(job)job.row=row;}
 $('.upload-list').innerHTML=data.items.filter(r=>state.showCancelledUploads||r.status!=='cancelled').map(uploadRow).join('');
 if(data.items.some(r=>r.status==='finalizing'))uploadPollTimer=setTimeout(()=>refreshUploadList(pid).catch(()=>{}),2500);
 else await refreshUploadPairs();
}
function hashUpload(job,chunkBytes){
 return new Promise((resolve,reject)=>{
  const worker=new Worker('/static/upload-hash-worker.js');job.worker=worker;
  job.cancelHash=()=>{worker.terminate();reject(new DOMException('已暂停','AbortError'));};
  worker.onmessage=e=>{if(e.data.type==='progress'){job.hashed=e.data.bytes;uploadRefresh();}else{worker.terminate();job.worker=null;job.cancelHash=null;e.data.type==='done'?resolve(e.data):reject(Error(e.data.message));}};
  worker.onerror=()=>{worker.terminate();reject(Error('无法校验文件，请重新选择原文件'));};
  worker.postMessage({file:job.file,chunkBytes});
 });
}
function uploadChunk(job,index,blob){
 return new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest();job.xhr=xhr;xhr.open('PUT',uploadPath(job.pid,'/'+id(job.id)+'/chunks/'+index));
  xhr.setRequestHeader('Content-Type','application/octet-stream');xhr.setRequestHeader('X-BioFlow-CSRF',bioflowCsrf);
  const started=performance.now(),accepted=job.accepted;
  xhr.upload.onprogress=e=>{job.bytes=accepted+e.loaded;job.speed=e.loaded/Math.max(.1,(performance.now()-started)/1000);uploadRefresh();};
  xhr.onload=()=>{job.xhr=null;let data;try{data=JSON.parse(xhr.responseText);}catch{data={detail:'服务器返回了无效响应'};}if(xhr.status>=200&&xhr.status<300)resolve(data);else reject(Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail)));};
  xhr.onerror=()=>{job.xhr=null;reject(Error('网络中断。已完成的分片保留，可继续上传。'));};
  xhr.onabort=()=>{job.xhr=null;reject(new DOMException('已暂停','AbortError'));};xhr.send(blob);
 });
}
async function runUpload(job){
 job.running=true;job.phase='hashing';job.error='';job.hashed=0;uploadRefresh();
 try{
  let row=await api(uploadPath(job.pid,'/'+id(job.id)));
  if(job.stopped||bioflowUser?.id!==job.user)return;
  job.row=row;
  if(row.status==='completed'){job.phase='completed';return;}
  if(job.file.size!==row.size_bytes)throw Error('文件大小不同，请选择原来的文件。');
  const config=row.fingerprint?row:await api(uploadPath(job.pid,'/config'));
  if(job.stopped||bioflowUser?.id!==job.user)return;
  const chunkBytes=config.chunk_bytes;
  const result=await hashUpload(job,chunkBytes);
  if(job.stopped)return;
  if(row.fingerprint&&row.fingerprint!==result.fingerprint)throw Error('文件内容不同，已保留原来的分片。请选择原文件。');
  if(!row.fingerprint){
   if(row.status==='paused'||row.status==='cancelled')await api(uploadPath(job.pid,'/'+id(job.id)+'/resume'),'POST',{});
   row=await api(uploadPath(job.pid),'POST',{draft_id:job.id,name:row.name,size_bytes:job.file.size,chunk_bytes:chunkBytes,hashes:result.hashes,fingerprint:result.fingerprint});
   if(row.id!==job.id){const existing=uploadJobs.get(uploadJobKey(job.pid,row.id));uploadJobs.delete(job.key);if(existing?.running){job.phase='completed';if(state.page==='uploads'&&state.project===job.pid)await render();return;}job.id=row.id;job.key=uploadJobKey(job.pid,row.id);uploadJobs.set(job.key,job);if(state.page==='uploads'&&state.project===job.pid)await render();}
  }
  if(job.stopped)return;
  if(row.status==='completed'){job.phase='completed';return;}
  row=await api(uploadPath(job.pid,'/'+id(job.id)+'/resume'),'POST',{});
  row=await api(uploadPath(job.pid,'/'+id(job.id)));
  job.row=row;job.accepted=row.received_bytes;job.bytes=job.accepted;job.phase='uploading';uploadRefresh();
  const received=new Set(row.received_chunks);
  for(let i=0;i<result.hashes.length;i++){
   if(job.stopped)return;
   if(received.has(i))continue;
   row=await uploadChunk(job,i,job.file.slice(i*chunkBytes,Math.min(job.file.size,(i+1)*chunkBytes)));
   job.row=row;job.accepted=row.received_bytes;job.bytes=job.accepted;
  }
  if(job.stopped)return;
  job.phase='finalizing';uploadRefresh();
  row=await api(uploadPath(job.pid,'/'+id(job.id)+'/complete'),'POST',{});job.row=row;job.phase='completed';job.bytes=job.file.size;
  if(state.page==='uploads'&&state.project===job.pid)refreshUploadPairs().catch(e=>notify(e.message));
  if(bioflowUser?.id===job.user){
   const list=await api(`/api/projects/${id(job.pid)}/assets`);const asset=list.items.find(a=>a.id===row.asset_id);
   if(bioflowUser?.id===job.user&&asset&&homeDraft.project===job.pid&&!homeDraft.files.some(f=>f.id===asset.id))homeDraft.files.push(asset);
   const projects=await api('/api/projects');if(bioflowUser?.id===job.user)state.projects=projects;
  }
 }catch(e){if(job.row?.status==='completed'){job.phase='completed';notify('文件已保存，可刷新项目资料查看。');}else if(e.name!=='AbortError'){job.error=e.message;job.phase='error';}else job.phase='paused';}
 finally{job.running=false;uploadActive--;uploadRefresh();pumpUploads();}
}
function pumpUploads(){
 if(!bioflowUser)return;
 for(const job of uploadJobs.values())if(uploadActive<2&&job.user===bioflowUser.id&&job.phase==='queued'&&!job.running&&!job.stopped){uploadActive++;runUpload(job);}
}
async function addUploads(files,pid){
 const owner=activeUploadUser();let added=0,issue='';
 for(const file of files){
  if(activeUploadUser()!==owner)break;
  if(file.size===0){issue='空文件无法上传：'+file.name;continue;}
  try{
   const row=await api(uploadPath(pid,'/drafts'),'POST',{name:file.name,size_bytes:file.size});if(activeUploadUser()!==owner)break;
   const key=uploadJobKey(pid,row.id);uploadJobs.set(key,{key,pid,id:row.id,user:owner,file,phase:'queued',running:false,stopped:false,bytes:0,accepted:0,speed:0});added++;
  }catch(e){issue=e.message;break;}
 }
 if(added&&state.project===pid&&activeUploadUser()===owner)await navigate('uploads');pumpUploads();if(issue&&activeUploadUser()===owner)notify(issue);
}
root.addEventListener('change',async e=>{
 const input=e.target;if(!['home-file-input','project-file-input','queue-file-input','queue-reselect-input'].includes(input.id))return;
 e.preventDefault();e.stopImmediatePropagation();const files=[...input.files];input.value='';if(!files.length)return;
 try{
  if(input.id==='queue-reselect-input'){
   const uid=uploadSelectFor,pid=state.project;const key=uploadJobKey(pid,uid);
   const row=await api(uploadPath(pid,'/'+id(uid)));if(files[0].size!==row.size_bytes)throw Error('请选择这条记录对应的原文件，当前文件大小不同。');
   const old=uploadJobs.get(key);if(old?.running)throw Error('请先等待当前操作完成');
   uploadJobs.set(key,{key,pid,id:uid,user:bioflowUser.id,file:files[0],phase:'queued',running:false,stopped:false,bytes:row.received_bytes,accepted:row.received_bytes,speed:0});pumpUploads();uploadRefresh();return;
  }
  if(input.id==='home-file-input'){if(!homeDraft.text&&!homeDraft.project)homeDraft.text=files[0].name.replace(/\.[^.]+$/,'');await ensureHomeProject();}
  await addUploads(files,state.project);
 }catch(err){notify(err.message);}
},true);
actions['upload-manager']=async()=>{if(!current())await ensureHomeProject();await navigate('uploads');};
actions['upload-add']=()=>$('#queue-file-input').click();
actions['upload-assets']=()=>{state.assetLegacy=false;return navigate('assets');};
actions['upload-cancelled']=()=>{state.showCancelledUploads=!state.showCancelledUploads;return render();};
async function haltUpload(uid,cancel){
 const pid=state.project,job=uploadJobs.get(uploadJobKey(pid,uid));
 if(job){job.stopped=true;job.phase=cancel?'cancelled':'paused';job.xhr?.abort();job.cancelHash?.();}
 await api(uploadPath(pid,'/'+id(uid)+(cancel?'/cancel':'/pause')),'POST',{});await render();
}
actions['upload-pause']=el=>haltUpload(el.dataset.id,false);
actions['upload-cancel']=el=>haltUpload(el.dataset.id,true);
actions['upload-resume']=async el=>{
 const uid=el.dataset.id,job=uploadJobs.get(uploadJobKey(state.project,uid));
 if(job?.running)throw Error('正在保存上传状态，请稍后继续');
 if(job?.file){await api(uploadPath(state.project,'/'+id(uid)+'/resume'),'POST',{});job.stopped=false;job.phase='queued';pumpUploads();uploadRefresh();}
 else{uploadSelectFor=uid;$('#queue-reselect-input').click();}
};
function saveUploadPairDraft(form){
 form.dataset.dirty='true';state.uploadPairDrafts||={};
 state.uploadPairDrafts[state.project]=[...form.querySelectorAll('.upload-pair-row')].map(r=>({sample:r.querySelector('[name=sample]').value,read1:r.querySelector('[name=read1]').value,read2:r.querySelector('[name=read2]').value}));
}
actions['upload-pair-remove']=el=>{const form=el.closest('form');el.closest('.upload-pair-row').remove();saveUploadPairDraft(form);};
actions['upload-pair-add']=()=>{
 const options='<option value="">选择文件</option>'+(state.uploadCandidates||[]).map(f=>`<option value="${esc(f.id)}">${esc(f.name)}</option>`).join('');
 $('.upload-pair-rows').insertAdjacentHTML('beforeend',`<div class="upload-pair-row"><label>样本编号<input name="sample" required pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,127}"></label><label>Read 1<select name="read1" required>${options}</select></label><label>Read 2<select name="read2" required>${options}</select></label>${button('移除此行','upload-pair-remove')}</div>`);saveUploadPairDraft($('.upload-pair-form'));
};
async function refreshUploadPairs(){
 const pid=state.project,data=await api(uploadPath(pid));if(state.page!=='uploads'||state.project!==pid)return;
 const area=$('#upload-pair-area');if(!area)return;
 const form=area.querySelector('form');
 if(form&&[...form.querySelectorAll('button')].some(b=>b.disabled))return;
 if(form?.dataset.dirty==='true'){
  const used=new Set(data.pairs.flatMap(p=>[p.read1,p.read2]));const files=data.files.filter(f=>!used.has(f.id));
  state.uploadCandidates=files;form.querySelectorAll('select').forEach(s=>{const selected=s.value;s.innerHTML='<option value="">选择文件</option>'+files.map(f=>`<option value="${esc(f.id)}">${esc(f.name)}</option>`).join('');s.value=selected;});saveUploadPairDraft(form);
 }else area.innerHTML=pairForm(data);
}
actions['upload-pair-refresh']=refreshUploadPairs;
root.addEventListener('input',e=>{const f=e.target.closest('[data-form="upload-pairs"]');if(f)saveUploadPairDraft(f);});
root.addEventListener('change',e=>{const f=e.target.closest('[data-form="upload-pairs"]');if(f)saveUploadPairDraft(f);});
forms['upload-pairs']=async form=>{
 const pairs=[...form.querySelectorAll('.upload-pair-row')].map(r=>({sample_id:r.querySelector('[name=sample]').value.trim(),read1:r.querySelector('[name=read1]').value,read2:r.querySelector('[name=read2]').value}));
 if(!pairs.length)throw Error('请先整理至少一个完整的双端样本');
 const result=await api(uploadPath(state.project,'/pairs'),'POST',{name:form.elements.namedItem('dataset_name').value.trim(),pairs});state.manifest=result.manifest_path;if(state.uploadPairDrafts)delete state.uploadPairDrafts[state.project];await render();notify(result.sample_count+' 个样本配对完成，已建立数据集。可进入分析。');
};
const uploadsAssetsView=views.assets;
views.assets=async()=>{const html=await uploadsAssetsView();return html.replace('<div class="asset-toolbar">',`<div class="row upload-entry">${button('上传列表与文件配对','upload-manager')}</div><div class="asset-toolbar">`);};
actions['asset-sequencing']=()=>navigate('uploads');
actions.sequencing=()=>navigate('uploads');
forms.document=async form=>{const file=form.elements.namedItem('file').files[0];if(!file)throw Error('请选择文献文件');await addUploads([file],state.project);};
const uploadsHomeView=views.home;
views.home=async()=>{const html=await uploadsHomeView();return html+`<div class="home-upload-entry">${button('查看上传列表与文件配对','upload-manager')}</div>`;};
const uploadsSignout=actions.signout;
actions.signout=async()=>{for(const job of uploadJobs.values()){job.stopped=true;job.xhr?.abort();job.cancelHash?.();}uploadJobs.clear();await uploadsSignout();};

actions['upload-analysis']=async el=>{const data=await service('analysis','/api/datasets/'+id(el.dataset.dataset)+'/reuse','POST',{});state.manifest=data.manifest_path;await navigate('analysis');};
