/* Native Skill workspace: one total-system sidebar and one account session. */
(() => {
  const pageKey = 'skill-center';
  const entries = [
    ['/skills', 'Skill 库', '◇'], ['/sources', '来源与导入', '↥', true],
    ['/algorithms', '算法库', '⌘'], ['/plots', '绘图示例与模板', '▥'], ['/runs', '运行记录', '◷']
  ];
  let route = new URLSearchParams(location.search).get('skill_center') || '/skills';
  let pendingInitial = new URLSearchParams(location.search).get('skill_center');
  let mounted = null, modulePromise = null, version = 0, legacyObserver = null;
  const previousRender = render;
  const routeEntry = () => entries.find(e => route.split('?')[0].startsWith(e[0])) || entries[0];

  function syncSidebar() {
    const rail = root.querySelector('.rail');
    if (!rail.querySelector('[data-native-center-rail]')) {
      const button = document.createElement('button');
      button.className = 'navitem cursor-interaction';
      button.dataset.action = 'native-skill-route'; button.dataset.route = '/skills';
      button.dataset.nativeCenterRail = ''; button.innerHTML = '<span class="symbol">◇</span>技能';
      rail.insertBefore(button, rail.querySelector('.spacer'));
    }
    let group = root.querySelector('.native-skills-nav');
    if (!group) {
      group = document.createElement('nav'); group.className = 'native-skills-nav'; group.setAttribute('aria-label','技能与算法导航');
      const projects = root.querySelector('.projects');
      projects.insertBefore(group, projects.querySelector('.project-list'));
    }
    group.hidden = !bioflowUser || state.page !== pageKey;
    group.innerHTML = '<div class="caption">技能与算法</div>' + entries.filter(e => !e[3] || bioflowUser?.role === 'developer').map(e =>
      `<button type="button" data-action="native-skill-route" data-route="${e[0]}" class="${state.page===pageKey && routeEntry()[0]===e[0]?'selected':''}"><span aria-hidden="true">${e[2]}</span>${e[1]}</button>`).join('');
    rail.querySelector('[data-native-center-rail]').classList.toggle('active',state.page===pageKey);
  }

  function updateRoute(url, push = true) {
    route = url;
    root.querySelector('[data-native-skill-root]')?.toggleAttribute('data-library-overview',['/skills','/algorithms'].includes(route.split('?')[0]));
    names[pageKey] = routeEntry()[1];
    const params = new URLSearchParams(location.search); params.set('skill_center',url);
    if (push) history.pushState({skillCenterPath:url},'', '/?'+params.toString());
    syncSidebar();
    const crumb = root.querySelector('.crumb');
    if (state.page===pageKey && crumb) crumb.textContent = 'BioFlow / '+routeEntry()[1];
  }

  views[pageKey] = async () => '<div data-native-skill-root aria-label="技能与算法工作区"></div>';
  actions['native-skill-route'] = async el => {
    updateRoute(el.dataset.route);
    await navigate(pageKey);
  };
  render = async function () {
    const ticket = ++version;
    if (legacyObserver) { legacyObserver.disconnect(); legacyObserver=null; }
    if(state.page==='models' && state.libraryKind==='skills'){state.libraryKind='models';route='/skills';state.page=pageKey;updateRoute(route);}
    if (pendingInitial && bioflowUser) { route=pendingInitial; state.page=pageKey; pendingInitial=null; }
    if (mounted) { mounted.unmount(); mounted = null; }
    names[pageKey] = routeEntry()[1];
    if (state.page === pageKey && bioflowUser) {
      clearTimeout(pollTimer); ++generation; chrome();
      root.querySelector('.content').innerHTML = await views[pageKey]();
    } else await previousRender();
    syncSidebar();
    if (state.page !== pageKey || !bioflowUser || ticket !== version) {
      if (state.page !== pageKey && !pendingInitial && new URLSearchParams(location.search).has('skill_center')) {
        const params = new URLSearchParams(location.search); params.delete('skill_center');
        history.replaceState({},'',location.pathname+(params.size?'?'+params.toString():''));
      }
      return;
    }
    const host = root.querySelector('[data-native-skill-root]');
    if (!host) return;
    try {
      if (!modulePromise) modulePromise = api('/skill-center/entry.json').then(async entry => ({entry, module: await import(entry.module)})).catch(error => { modulePromise=null; throw error; });
      const {entry,module} = await modulePromise;
      if (ticket !== version || !host.isConnected) return;
      host.toggleAttribute('data-library-overview',['/skills','/algorithms'].includes(route.split('?')[0]));
      const shadow = host.attachShadow({mode:'open'});
      for (const href of entry.css) {
        const link = document.createElement('link'); link.rel='stylesheet'; link.href=href; shadow.append(link);
      }
      const style = document.createElement('style');
      style.textContent = `:host{display:block;font-family:inherit;font-size:14px;color:#243329;--green:#527d60;--dark:#233b30;--border:#dde5d9;--muted:#788478;--surface:#fff}
        .shell.embedded{display:block;min-height:0;background:transparent}.embedded>.main{margin:0;width:100%;min-height:0}.embedded main{padding:0;max-width:none}
        .native-public-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:18px;margin-top:20px}
        .native-public-card{display:block;text-align:left;white-space:normal;min-width:0}.native-public-card p{font-weight:400;line-height:1.7;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}.skill-search{width:100%}
        .page-heading{padding-right:42px;display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap;margin:0 0 22px}.page-heading>div:first-child{flex:1;min-width:240px}.page-heading p{line-height:1.7}
        :host([data-library-overview]) .page-heading h1,:host([data-library-overview]) .page-heading .eyebrow{display:none}
        :host([data-library-overview]) .page-heading>div>p:last-child{margin:0;color:#748570}
        .native-public-card{padding:20px;border-radius:14px;display:flex;flex-direction:column;gap:8px;align-items:flex-start;min-height:190px;height:100%;box-sizing:border-box}.native-public-card h2{font-size:19px;line-height:1.5;margin:0}.native-public-card p{margin:0;flex:1;-webkit-line-clamp:3}.native-public-card>span:last-child{margin-top:8px}.native-public-grid{align-items:stretch}.tag{max-width:100%;white-space:normal}
        [data-legacy-launch]{display:flex;gap:8px;flex-wrap:wrap;max-width:100%}`;
      shadow.append(style);
      const container = document.createElement('div'); shadow.append(container);
      mounted = module.mountSkillCenter(container,{path:route,role:bioflowUser.role,csrf:()=>bioflowCsrf,onNavigate:updateRoute});
      const originalSkills=await api('/api/skills');
      if(ticket!==version||!host.isConnected)return;
      const attachLaunch=()=>{
        const sid=route.split('?')[0].split('/')[2];
        if(!sid||!route.startsWith('/skills/'))return;
        const bound=originalSkills.filter(s=>s.center_skill_id&&s.center_skill_id===sid);
        const heading=shadow.querySelector('.page-heading');
        if(!heading||!bound.length||heading.querySelector('[data-legacy-launch]'))return;
        const box=document.createElement('div');box.dataset.legacyLaunch='';box.className='row';
        for(const entry of bound){
          const launch=document.createElement('button');launch.type='button';launch.className='button primary';
          launch.textContent=entry.center_binding_mode==='agent_handoff'?'准备项目任务交接':'在当前项目中使用';
          launch.title=entry.name;
          launch.onclick=async()=>{try{
            if(!current()){notify('请先在左侧选择或新建研究项目。');return;}
            const live=(await api('/api/skills')).find(s=>s.name===entry.name);
            if(!live||live.enabled===0){notify('该 Skill 已停用或删除。');return;}
            if(live.chart_id)await actions['ability-chart']({dataset:{chart:live.chart_id}});
            else await actions.skill({dataset:{name:live.name,version:live.version,keys:JSON.stringify(live.input_keys)}});
          }catch(error){notify(error.message);}};
          box.append(launch);
        }
        heading.append(box);
      };
      legacyObserver=new MutationObserver(attachLaunch);legacyObserver.observe(container,{childList:true,subtree:true});attachLaunch();
      root.querySelector('.scope').textContent = '公共能力库 · '+bioflowUser.username+' 的数据与运行记录';
    } catch (error) { const target=host.shadowRoot || host; const message=document.createElement("p"); message.textContent=error.message; target.append(message); }
  };
  window.addEventListener('popstate',async () => {
    const path = new URLSearchParams(location.search).get('skill_center');
    if (path) { updateRoute(path,false); await navigate(pageKey); }
    else if (state.page===pageKey) await navigate('home');
  });
  const priorLibraryKind=actions['library-kind'];
  actions['library-kind']=async el=>{
    if(el.dataset.kind==='skills'){state.libraryKind='models';updateRoute('/skills');await navigate(pageKey);}
    else await priorLibraryKind(el);
  };
  syncSidebar();
  if (pendingInitial && bioflowUser) navigate(pageKey).catch(error => notify(error.message));
})();
