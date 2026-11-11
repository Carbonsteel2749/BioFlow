/* Task navigation and project spaces. The native Skills sidebar stays intact. */
(() => {
  names.assets='数据与运行结果';names.charts='图表工作台';names.literature='文献';
  const navPaths={
    models:'<path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Zm-8 4.5L12 12l8-4.5M12 12v9M8 5.3l8 4.5"/>',
    literature:'<path d="M12 6.5C9.5 4.7 6.5 4.2 3 5v14c3.5-.8 6.5-.3 9 1.5 2.5-1.8 5.5-2.3 9-1.5V5c-3.5-.8-6.5-.3-9 1.5Zm0 0v14M6 9l3 .8M15 9.8l3-.8M6 13l3 .8M15 13.8l3-.8"/>',
    assets:'<ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6M4 11.5v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    analysis:'<rect x="3" y="3" width="6" height="6" rx="2"/><rect x="15" y="15" width="6" height="6" rx="2"/><path d="M9 6h6a3 3 0 0 1 3 3v6M6 9v6a3 3 0 0 0 3 3h6"/>',
    charts:'<path d="M3 3v16a2 2 0 0 0 2 2h16"/><rect x="7" y="11" width="3" height="6" rx="1.3"/><rect x="13" y="5" width="3" height="12" rx="1.3"/><path d="M19 9v8"/>',
    writing:'<path d="m15 4 5 5M5 14 15.3 3.7a2.1 2.1 0 0 1 3 0l2 2a2.1 2.1 0 0 1 0 3L10 19l-7 2 2-7ZM5 14l5 5"/>',
    'skill-center':'<path d="M9 3H5a2 2 0 0 0-2 2v4h3a3 3 0 1 1 0 6H3v4a2 2 0 0 0 2 2h4v-3a3 3 0 1 1 6 0v3h4a2 2 0 0 0 2-2v-4h-3a3 3 0 1 1 0-6h3V5a2 2 0 0 0-2-2h-4v3a3 3 0 1 1-6 0V3Z"/>'
  };
  const navSvg=key=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${navPaths[key]}</svg>`;
  const cache = new Map(), inflight = new Map(), statusLabels = {completed:'已完成',succeeded:'已完成',running:'运行中',failed:'失败',queued:'排队中',cancelled:'已取消',pending:'待运行'};
  let collapsed = localStorage.getItem('bioflow.projects.collapsed') === 'true', selected = '', spaceQuery = '', searchVersion = 0, searchTimer, sidebarKey = '';
  const icon = (name) => name === 'plus' ? '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>' : name === 'search' ? '<svg viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg>' : '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16m7 5-3 3 3 3"/></svg>';
  const projectUrl = (pid, path) => '/api/projects/' + id(pid) + path;
  async function cached(key, loader, ttl = 10000) {
    key = (bioflowUser?.id || '') + ':' + key;
    if (cache.has(key) && Date.now() - cache.get(key).at < ttl) return cache.get(key).data;
    if (inflight.has(key)) return inflight.get(key);
    const promise = loader().then(data => { cache.set(key, {data, at:Date.now()}); return data; }).finally(() => inflight.delete(key));
    inflight.set(key,promise); return promise;
  }
  async function projectTasks(pid, fresh = false) {
    return cached('tasks:'+pid, async () => {
      const results = await Promise.allSettled([
        (async () => {const [ids, all] = await Promise.all([api(projectUrl(pid,'/links/task')), api('/services/analysis/api/tasks')]); const owned = new Set(ids); return items(all).filter(t=>owned.has(t.id)).map(t=>({...t, type:'analysis'}));})(),
        (async () => {let all=[],offset=0; for(;;){const data=await api(projectUrl(pid,'/analysis/workflows/runs?limit=100&offset='+offset));all.push(...data.items.map(t=>({...t,id:t.task_id,type:'workflow'})));offset+=data.items.length;if(!data.items.length||offset>=data.total)break;}return all;})()
      ]);
      if(results.every(r=>r.status==='rejected'))throw Error('任务目录读取失败，请重试');
      const merged = new Map(); for(const r of results)if(r.status==='fulfilled')for(const t of r.value)merged.set(t.id,t);
      return {items:[...merged.values()],partial:results.some(r=>r.status==='rejected')};
    }, fresh ? 0 : 10000);
  }
  function taskRows(list) {
    return list.map(t=>`<button class="task-nav-item ${state.task===t.id?'chosen':''}" data-action="sidebar-task" data-id="${esc(t.id)}" data-type="${t.type}"><span>${esc(t.name||t.id)}</span><small>${esc(statusLabels[t.status]||t.status||'状态待更新')}</small></button>`).join('') || '<p class="caption task-empty">暂无任务，点击右上角 ＋ 开始配置。</p>';
  }
  async function fillTasks(force = false) {
    const pid=state.project,uid=bioflowUser?.id,host=root.querySelector('.task-sidebar-list'); if(!host)return;
    const key=uid+':'+pid;
    if(!force && sidebarKey===key && host.dataset.loaded===key && Date.now()-Number(host.dataset.loadedAt)<10000)return;
    sidebarKey=key;host.dataset.loaded='';
    root.querySelector('.task-space-name').textContent=current()?.name||'尚未选择项目';
    if(!uid||!pid){host.innerHTML='<p class="caption task-empty">从头像进入项目空间。</p>';return;}
    host.innerHTML='<p class="caption task-empty">正在读取任务…</p>';
    try{const data=await projectTasks(pid,force);if(state.project!==pid||bioflowUser?.id!==uid)return;host.innerHTML=taskRows(data.items)+(data.partial?'<p class="caption">部分任务暂未读到</p>':'');host.dataset.loaded=key;host.dataset.loadedAt=String(Date.now());}
    catch(e){if(state.project===pid&&bioflowUser?.id===uid)host.innerHTML=`<p class="caption">${esc(e.message)}</p>${button('重新读取','sidebar-retry')}`;}
  }
  function syncLayout() {
    const ordinary=state.page!=='skill-center';root.classList.toggle('project-space-layout',ordinary);root.classList.toggle('projects-collapsed',ordinary&&collapsed);
    const rail=root.querySelector('.rail');
    if(!rail.querySelector('[data-page="literature"]')){const control=document.createElement('button');control.className='navitem cursor-interaction';control.dataset.page='literature';control.innerHTML='<span class="symbol"></span>文献';rail.insertBefore(control,rail.querySelector('[data-page="assets"]'));}
    root.querySelectorAll('.rail .navitem').forEach(control=>{
      const key=control.dataset.page||(control.hasAttribute('data-native-center-rail')?'skill-center':'');
      const label=key==='assets'?'数据与运行结果':key==='charts'?'图表':key==='literature'?'文献':[...control.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim();
      if(label){control.title=label;control.setAttribute('aria-label',label);}
      const symbol=control.querySelector('.symbol');if(symbol&&navPaths[key]&&symbol.dataset.navIcon!==key){symbol.innerHTML=navSvg(key);symbol.dataset.navIcon=key;}
    });
    const moduleTitle={assets:'数据与运行结果',charts:'图表工作台',literature:'文献'}[state.page];
    root.classList.toggle('module-title-layout',!!moduleTitle);
    let headerActions=root.querySelector('.module-header-actions');
    if(!headerActions){headerActions=document.createElement('div');headerActions.className='module-header-actions';root.querySelector('.topbar').insertBefore(headerActions,root.querySelector('.top-actions'));}
    headerActions.hidden=!moduleTitle;
    if(moduleTitle){root.querySelector('.crumb').innerHTML=`<h1 class="module-title">${moduleTitle}</h1>`;headerActions.innerHTML=state.page==='charts'?button('刷新','refresh'):button(state.page==='literature'?'＋ 上传文献':'＋ 上传资料','task-assets-upload',`class="primary" ${current()&&(state.page==='literature'||state.task)?'':'disabled'}`);}

    let sidebar=root.querySelector('.task-sidebar');
    if(!sidebar){sidebar=document.createElement('aside');sidebar.className='task-sidebar';sidebar.id='bioflow-task-sidebar';sidebar.setAttribute('aria-label','当前项目任务');sidebar.innerHTML=`<div class="task-sidebar-heading"><strong>BioFlow</strong><button type="button" data-action="sidebar-new-task" aria-label="新建任务" title="新建任务">${icon('plus')}</button><button type="button" data-action="workspace-search" aria-label="搜索所有项目内容" title="搜索所有项目内容">${icon('search')}</button></div><button type="button" class="task-space-name" data-action="sidebar-project-overview" title="打开项目概览"></button><div class="task-sidebar-list"></div>`;root.querySelector('.shell').insertBefore(sidebar,root.querySelector('.shell>.main'));}
    sidebar.hidden=!ordinary;
    let toggle=root.querySelector('[data-action="toggle-project-sidebar"]');
    if(!toggle){toggle=document.createElement('button');toggle.type='button';toggle.dataset.action='toggle-project-sidebar';toggle.className='project-sidebar-toggle';root.querySelector('.topbar').prepend(toggle);}
    toggle.hidden=!ordinary;toggle.title=collapsed?'展开任务栏':'收起任务栏';toggle.setAttribute('aria-label',toggle.title);toggle.setAttribute('aria-expanded',String(!collapsed));toggle.setAttribute('aria-controls',sidebar.id);toggle.innerHTML=icon('sidebar');
    if(ordinary){root.querySelectorAll('.task-nav-item').forEach(e=>e.classList.toggle('chosen',e.dataset.id===state.task));fillTasks();}
  }
  const previousChrome=chrome;chrome=function(){previousChrome();syncLayout();};
  actions['toggle-project-sidebar']=()=>{collapsed=!collapsed;localStorage.setItem('bioflow.projects.collapsed',String(collapsed));syncLayout();};
  actions['sidebar-retry']=()=>fillTasks(true);
  actions['sidebar-project-overview']=()=>navigate('overview');
  actions['sidebar-new-task']=async()=>{
    if(!current())return showSpaces();
    state.task='';$('.overlay').hidden=true;state.page='analysis';
    if(actions['unified-new'])await actions['unified-new']();else await render();
    $('.content [name="name"]')?.focus();
  };
  actions['sidebar-task']=async el=>{
    const pid=state.project,tid=el.dataset.id;
    state.task=tid;state.page='analysis';$('.overlay').hidden=true;await render();
    if(state.page!=='analysis'||state.project!==pid||state.task!==tid)return;
    if(el.dataset.type==='workflow')await actions['unified-history']({dataset:{id:el.dataset.id}});else await showTask(el.dataset.id);
    root.querySelectorAll('.task-nav-item').forEach(e=>e.classList.toggle('chosen',e.dataset.id===state.task));
  };
  function spaceRows(){const list=state.projects.filter(p=>(p.name+' '+p.description).toLowerCase().includes(spaceQuery.toLowerCase().trim()));return list.map(p=>`<div class="space-card ${p.id===selected?'selected':''}"><label class="space-choice"><input type="radio" name="project-space-choice" value="${esc(p.id)}" ${p.id===selected?'checked':''}><span><strong>${esc(p.name)}</strong>${p.id===state.project?'<small>当前空间</small>':''}<span class="space-description">${esc(p.description||'研究工作空间')}</span></span></label><div class="space-manage">${button('编辑','project-edit',`data-id="${esc(p.id)}"`)}${button('删除','project-delete',`data-id="${esc(p.id)}"`)}</div></div>`).join('')||'<p class="caption">未找到项目，可点击新建项目。</p>';}
  function showSpaces(){selected=state.project;spaceQuery='';modal('项目空间',`<div class="space-account"><span>${esc(bioflowUser.username)}</span><div>${button('修改密码','password')}${button('退出登录','signout')}</div></div><div class="space-toolbar"><input data-space-search aria-label="搜索项目空间" placeholder="搜索项目空间">${button('＋ 新建项目','newproject')}</div><div class="space-list" role="group" aria-label="选择项目空间">${spaceRows()}</div><div class="space-footer"><span data-space-selection></span>${button('切换项目空间','space-switch','class="primary"')}</div>`);const d=$('.overlay .modal');d.classList.add('project-space-dialog');d.setAttribute('role','dialog');d.setAttribute('aria-modal','true');d.setAttribute('aria-label','项目空间');updateSpaceSelection();$('[data-space-search]').focus();}
  function updateSpaceSelection(){const p=state.projects.find(p=>p.id===selected);$('[data-space-selection]').textContent=p?'已选择：'+p.name:'请选择项目';$('[data-action="space-switch"]').disabled=!p;}
  async function switchSpace(pid){if(!state.projects.some(p=>p.id===pid))throw Error('项目空间不可用');state.project=pid;state.task='';state.session='';state.chapter='';state.manifest='';state.assetTask='';state.assetPage=0;state.page='home';localStorage.setItem('bioflow.project',pid);localStorage.setItem('bioflow.project.'+bioflowUser.id,pid);sidebarKey='';$('.overlay').hidden=true;await render();}
  actions['space-switch']=()=>switchSpace(selected);
  window.addEventListener('click',event=>{const control=event.target.closest?.('.avatar,[data-action="account"]');if(!control||!root.contains(control)||!bioflowUser)return;event.preventDefault();event.stopImmediatePropagation();showSpaces();},true);
  async function projectMetadata(pid){return cached('metadata:'+pid,async()=>{let rows=[],offset=0;for(;;){const data=await api(projectUrl(pid,'/assets?limit=100&offset='+offset));rows.push(...data.items);offset+=data.items.length;if(!data.items.length||offset>=data.total)break;}return rows;},30000);}
  let searchResults=[];
  actions['workspace-search']=()=>{++searchVersion;searchResults=[];modal('搜索所有项目内容','<input data-workspace-search aria-label="搜索任务、文献、数据和结果" placeholder="搜索任务、文献、数据、图表或稿件"><p class="caption">搜索当前账户的全部项目，结果标注所属项目空间。</p><div class="workspace-search-results" aria-live="polite"></div>');$('.modal').classList.add('workspace-search-dialog');$('[data-workspace-search]').focus();};
  async function searchAll(term){
    const version=++searchVersion,uid=bioflowUser?.id,host=$('.workspace-search-results');if(!host)return;
    term=term.trim().toLowerCase();searchResults=[];if(!term){host.innerHTML='';return;}host.innerHTML='<p>正在搜索项目目录…</p>';
    const found=[],failures=[];let next=0;const projects=[...state.projects];
    await Promise.all(Array.from({length:Math.min(3,projects.length)},async()=>{while(next<projects.length){if(version!==searchVersion||bioflowUser?.id!==uid)return;const p=projects[next++];if((p.name+' '+p.description).toLowerCase().includes(term))found.push({project:p,type:'project',name:p.name});const results=await Promise.allSettled([projectTasks(p.id),projectMetadata(p.id)]);if(results[0].status==='fulfilled'){for(const t of results[0].value.items)if((t.name+' '+t.id).toLowerCase().includes(term))found.push({project:p,type:t.type,name:t.name||t.id,item:t});if(results[0].value.partial)failures.push(p.name+'（部分任务）');}else failures.push(p.name+'（任务）');if(results[1].status==='fulfilled'){for(const a of results[1].value)if([a.name,a.summary,a.description,a.sample_id,...(a.task_names||[])].filter(Boolean).join(' ').toLowerCase().includes(term))found.push({project:p,type:'asset',name:a.name,item:a});}else failures.push(p.name+'（资料）');}}));
    if(version!==searchVersion||bioflowUser?.id!==uid||!host.isConnected)return;
    searchResults=found.slice(0,100);host.innerHTML=`<p class="caption">找到 ${found.length} 项${found.length>100?'，显示前 100 项，请缩小搜索范围':''}${failures.length?' · 未完整读取：'+esc([...new Set(failures)].join('、')):''}</p>`+searchResults.map((r,i)=>`<button class="workspace-search-hit" data-action="workspace-search-open" data-index="${i}"><strong>${esc(r.name)}</strong><span>${esc(r.project.name)} · ${esc(r.type==='asset'?(assetLabels[r.item.kind]||'资料'):r.type==='project'?'项目空间':'任务')}</span></button>`).join('')+(found.length?'':'<p>未找到匹配内容。</p>');
  }
  actions['workspace-search-open']=async el=>{const r=searchResults[Number(el.dataset.index)];if(!r)return;const item=r.item;++searchVersion;await switchSpace(r.project.id);if(r.type==='project')return;if(r.type==='asset'){state.page=item.kind==='literature'?'literature':'assets';state.task=item.task_ids?.[0]||item.task_id||'';state.assetKind=item.kind;await render();await actions['asset-links']({dataset:{id:item.id}});}else await actions['sidebar-task']({dataset:{id:item.id,type:r.type}});};
  root.addEventListener('input',event=>{if(event.target.matches('[data-space-search]')){spaceQuery=event.target.value;$('.space-list').innerHTML=spaceRows();}if(event.target.matches('[data-workspace-search]')){clearTimeout(searchTimer);++searchVersion;const value=event.target.value;searchTimer=setTimeout(()=>searchAll(value).catch(e=>{const host=$('.workspace-search-results');if(host)host.textContent=e.message;}),350);}});
  root.addEventListener('change',event=>{if(event.target.name!=='project-space-choice')return;selected=event.target.value;root.querySelectorAll('.space-card').forEach(card=>card.classList.toggle('selected',card.querySelector('input').value===selected));updateSpaceSelection();});
  const previousApi=api;api=async function(path,method='GET',body){const result=await previousApi(path,method,body);if(method!=='GET'){cache.clear();sidebarKey='';const list=$('.task-sidebar-list');if(list)delete list.dataset.loaded;}return result;};
  const previousSignout=actions.signout;actions.signout=async()=>{++searchVersion;clearTimeout(searchTimer);cache.clear();searchResults=[];sidebarKey='';await previousSignout();};
  root.addEventListener('keydown',event=>{
    const dialog=event.target.closest('.project-space-dialog,.workspace-search-dialog');if(!dialog)return;
    if(event.key==='Escape'){$('.overlay').hidden=true;++searchVersion;root.querySelector('.avatar').focus();return;}
    if(event.key!=='Tab')return;const controls=[...dialog.querySelectorAll('button:not(:disabled),input')],first=controls[0],last=controls[controls.length-1];
    if(event.shiftKey&&event.target===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&event.target===last){event.preventDefault();first.focus();}
  });
  syncLayout();
})();

/* Task-scoped library with one upload entry. */
(() => {
  views.assets = async () => {
    const pid=state.project;
    const groups=await api(pa('/asset-tasks'));
    if(state.project!==pid||state.page!=='assets')return '';
    let task=groups.items.find(t=>t.id===state.task)||(!state.task?groups.items[0]:null);
    if(!task&&state.task){
      try{const flow=await api(pa('/analysis/workflows/runs/'+id(state.task)));task={id:state.task,name:flow.run.name||state.task,pendingIndex:true};}catch{}
    }
    if(task){state.task=task.id;root.querySelectorAll('.task-nav-item').forEach(e=>e.classList.toggle('chosen',e.dataset.id===task.id));}
    const tid=task?.id||'',key=pid+':'+tid;
    if(state.taskAssetKey!==key){state.taskAssetKey=key;state.assetPage=0;}
    state.assetTask=tid;state.assetTaskProject=pid;state.assetLegacy=false;
    const heading=task?`<div class="task-assets-context"><span>当前任务</span><strong>${esc(task.name)}</strong></div>`:'';
    const uploadButton=root.querySelector('.module-header-actions [data-action="task-assets-upload"]');if(uploadButton)uploadButton.disabled=!tid||!!task?.pendingIndex;
    if(task?.pendingIndex)return heading+'<div class="empty-state"><h2>任务资料索引待更新</h2><p>该流程任务的资料归属接口将在后台服务更新后启用，当前分析继续运行。</p></div>';
    if(!tid)return heading+'<div class="empty-state"><h2>请先选择或创建任务</h2><p>从左侧选择任务，查看对应的文献、数据和成果。</p></div>';
    const kind=state.assetKind&&state.assetKind!=='literature'?state.assetKind:'data';state.assetKind=kind;
    const data=await api(`/api/projects/${id(pid)}/assets?kind=${id(kind)}&limit=24&offset=${(state.assetPage||0)*24}&hide_unavailable=${!!state.assetReady}&task_id=${id(tid)}`);
    const pages=Math.max(1,Math.ceil(data.total/24));if(state.assetPage>=pages){state.assetPage=pages-1;return views.assets();}
    const tools=kind==='literature'?button('文献检索与问答','asset-literature'):kind==='charts'?button('新建绘图','asset-chart-editor'):'';
    return heading+`<div class="asset-toolbar"><div class="tabs">${Object.entries(assetLabels).filter(([k])=>k!=='literature').map(([k,label])=>button(`${label} · ${data.counts[k]||0}`,'asset-kind',`data-kind="${k}" class="${kind===k?'selected':''}"`)).join('')}</div>${tools?`<div class="row">${tools}</div>`:''}</div><input type="file" id="project-file-input" multiple hidden><div class="asset-summary"><span>已登记 ${data.counts[kind]||0} 项${data.syncing?' · 后台更新目录':''}${data.sync_error?' · 部分目录暂未更新':''}</span>${button('刷新目录','refresh')}${button(state.assetReady?'显示全部记录':'隐藏已知异常文件','asset-toggle')}</div><div class="asset-grid">${data.items.map(assetCard).join('')||'<div class="empty-state"><h2>当前任务暂无此类资料</h2><p>使用右上角“上传资料”添加文件。</p></div>'}</div><div class="asset-pagination">${state.assetPage>0?button('上一页','asset-prev'):''}<span>${(state.assetPage||0)+1} / ${pages}</span>${state.assetPage+1<pages?button('下一页','asset-next'):''}</div>`;
  };
  actions['task-assets-upload']=()=>{if(state.page!=='literature'&&!state.task)throw Error('请先选择任务');state.importPurpose=state.page==='literature'?'literature':'auto';$('#project-file-input').click();};
  const previousAdd=addUploads;
  addUploads=async function(files,pid,...rest){
    const context=['assets','literature'].includes(state.page)&&state.task&&pid===state.project?{task:state.task,user:bioflowUser?.id,pid}:null;
    const result=await previousAdd(files,pid,...rest);
    const form=$('[data-form="confirm-imports"]');if(!context||!form)return result;
    form.dataset.taskUpload=context.task;form.dataset.taskUploadUser=context.user;
    const select=form.elements.namedItem('task_id');
    if(![...select.options].some(o=>o.value===context.task)){const option=document.createElement('option');option.value=context.task;option.textContent=context.task;select.append(option);}select.value=context.task;select.closest('label').hidden=true;
    const note=document.createElement('p');note.className='muted';note.textContent='文件将保存到当前任务，确认每个文件的分类后上传。';form.prepend(note);
    return result;
  };
  const previousConfirm=forms['confirm-imports'];
  forms['confirm-imports']=async form=>{
    if(!form.dataset.taskUpload)return previousConfirm(form);
    if(form.dataset.taskUploadUser!==bioflowUser?.id)throw Error('账户已变化，请重新选择文件');
    const pid=researchState.pid,files=researchState.imports,tid=form.dataset.taskUpload;
    const selections=files.map((file,i)=>({file,purpose:form.elements.namedItem('purpose-'+i).value}));
    for(const item of selections)if(/\.(fastq|fq)(\.gz)?$/i.test(item.file.name)&&item.purpose!=='data')throw Error('FASTQ 文件请归入测序数据');
    const details={task_id:tid,result_type:form.elements.namedItem('result_type').value,software:form.elements.namedItem('software').value};
    $('.overlay').hidden=true;
    for(const item of selections)await addConfirmedUploads([item.file],pid,{purpose:item.purpose,details});
    researchState.imports=[];
  };
})();

/* Literature is a project module; data and results remain scoped to a task. */
(() => {
  async function knowledgePanel(){
    if(!state.literatureTools)return '';
    try{
      const bases=items(await service('literature','/api/knowledge-bases')),kb=current()?.knowledge_base_id;
      return `<section class="box literature-knowledge"><h2>项目知识库</h2><div class="grid two"><form data-form="bind-kb"><label>关联已有知识库<select name="kb" required>${bases.map(k=>`<option value="${esc(k.id)}" ${kb===k.id?'selected':''}>${esc(k.name)}</option>`).join('')}</select></label><button ${bases.length?'':'disabled'}>关联知识库</button></form><form data-form="new-kb"><label>新知识库名称<input name="name" required value="${esc(current()?.name||'')}"></label><button>创建并关联</button></form></div>${kb?'<form data-form="ask"><h2>文献问答</h2><textarea name="question" aria-label="文献问题" placeholder="输入研究问题" required></textarea><button class="primary">检索与回答</button><div id="answer"></div></form>':'<p class="muted">上传文献时将按项目建立知识库，也可以在这里关联已有知识库。</p>'}</section>`;
    }catch(error){return `<div class="note" role="alert">知识库服务读取失败：${esc(error.message)}</div>`;}
  }
  views.literature=async()=>{
    const pid=state.project,page=state.literaturePage||0;
    const [data,panel]=await Promise.all([api(`/api/projects/${id(pid)}/assets?kind=literature&limit=24&offset=${page*24}`),knowledgePanel()]);
    if(state.project!==pid||state.page!=='literature')return '';
    const pages=Math.max(1,Math.ceil(data.total/24));if(page>=pages){state.literaturePage=pages-1;return views.literature();}
    return `<div class="literature-toolbar"><p class="muted">${esc(current()?.name||'')} · 项目文献</p><div class="row">${button('个人文献库','personal-library')}${button(state.literatureTools?'收起知识库与问答':'知识库与问答','literature-tools')}${button('刷新目录','refresh')}</div></div>${panel}<input type="file" id="project-file-input" multiple hidden><div class="asset-summary"><span>已登记 ${data.total} 篇文献${data.syncing?' · 后台更新目录':''}</span></div><div class="asset-grid">${data.items.map(assetCard).join('')||'<div class="empty-state"><h2>还没有文献</h2><p>点击右上角“上传文献”，添加论文并建立项目知识库。</p></div>'}</div><div class="asset-pagination">${page>0?button('上一页','literature-page','data-delta="-1"'):''}<span>${page+1} / ${pages}</span>${page+1<pages?button('下一页','literature-page','data-delta="1"'):''}</div>`;
  };
  actions['model-use']=el=>navigate(el.dataset.target==='assets'?'literature':el.dataset.target);
  actions['literature-tools']=async()=>{state.literatureTools=!state.literatureTools;await render();};
  actions['literature-page']=async el=>{state.literaturePage=Math.max(0,(state.literaturePage||0)+Number(el.dataset.delta));await render();};
  actions['asset-literature']=async()=>{state.literatureTools=true;await navigate('literature');};
  actions['paper-upload']=async()=>{await navigate('literature');state.importPurpose='literature';$('#project-file-input').click();};
  actions['asset-kind']=async el=>{if(el.dataset.kind==='literature')return navigate('literature');state.assetKind=el.dataset.kind;state.assetPage=0;state.assetReady=state.assetKind!=='data';state.assetLegacy=false;state.chartEditor=false;await navigate('assets');};
  const chartView=views.charts;
  views.charts=async()=>{const html=await chartView();const template=document.createElement('template');template.innerHTML=html;template.content.querySelector('.section-head')?.remove();return template.innerHTML;};
})();

/* Compact catalogue presentation. */
(() => {
 names.literature='文献库';
 const previousChrome=chrome;
 chrome=function(){previousChrome();if(state.page==='literature'){root.querySelector('.module-title').textContent='文献库';root.querySelector('.module-header-actions').innerHTML='';}root.classList.toggle('compact-catalogue',['assets','charts','literature','personal-library'].includes(state.page));};
 for(const page of ['assets','charts','literature','personal-library']){
  const previous=views[page];views[page]=async()=>{
   const html=await previous(),t=document.createElement('template');t.innerHTML=html;
   if(page==='literature'){
    t.content.querySelector('.literature-toolbar>p')?.remove();
    const empty=t.content.querySelector('.empty-state p');if(empty)empty.textContent='在个人文献库上传文献，再关联到当前项目。';
   }
   if(page==='personal-library'){
    const upload=t.content.querySelector('[data-action="paper-upload"]');if(upload)upload.textContent='＋ 上传文献';
   }
   if(['assets','charts'].includes(page)){
    const search=document.createElement('input');search.type='search';search.className='catalogue-search';search.placeholder=page==='charts'?'搜索图表':'搜索数据与结果';search.setAttribute('aria-label',search.placeholder);search.dataset.catalogueSearch='';
    const bar=document.createElement('div');bar.className='catalogue-view-bar';bar.innerHTML=page==='charts'?'<strong>图表分类</strong><button data-action="research-existing">已生成的图表</button>':'<strong>任务资料</strong>';bar.append(search);t.content.prepend(bar);
   }
   return t.innerHTML;
  };
 }
 actions['paper-upload']=async()=>{modal('上传文献','<input type="file" id="project-file-input" multiple accept=".pdf,.bib,.ris,.enw,.txt"><p class="muted">选择文件后确认分类并上传到当前项目。</p>');state.importPurpose='literature';$('#project-file-input').click();};
 root.addEventListener('input',event=>{if(!event.target.matches('[data-catalogue-search]'))return;const term=event.target.value.trim().toLowerCase();root.querySelectorAll('.asset-card,.chart-type-card,.research-versions>.box').forEach(card=>card.hidden=!card.textContent.toLowerCase().includes(term));});
})();
