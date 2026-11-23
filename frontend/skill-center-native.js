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

  const libraryState={skills:{category:'全部',query:''},algorithms:{category:'全部',query:''}};
  const skillGroups=['数据整理与管理','分析与验证','绘图与可视化','文献与知识','论文写作','通用工具'];
  function skillGroup(row){const category=row.category||'';if(/可视|绘图|图表|配图/.test(category))return '绘图与可视化';if(/文献|引用/.test(category))return '文献与知识';if(/数据来源|数据库/.test(category))return '数据整理与管理';if(/论文|章节|Methods|研究设计|可用性/.test(category))return '论文写作';if(/分析|统计|验证|评估/.test(category))return '分析与验证';return '通用工具';}
  async function publicCatalogue(host,shadow,container,ticket){
    const kind=route.split('?')[0].slice(1);if(bioflowUser.role==='developer'||!['skills','algorithms'].includes(kind))return;
    const ui=libraryState[kind];let rows=[];
    if(kind==='skills'){for(let page=1;;page++){const data=await api('/skill-center/api/v1/skills?page_size=100&page='+page);rows.push(...data.items);if(!data.items.length||rows.length>=data.total)break;}}
    else rows=(await api('/skill-center/api/v1/workbench/algorithms')).items;
    if(ticket!==version||!host.isConnected)return;
    const groups=kind==='skills'?skillGroups:['降维','聚类','分类','特征选择'];
    const group=row=>kind==='skills'?skillGroup(row):row.category||'未分类';
    const counts=new Map();rows.forEach(row=>counts.set(group(row),(counts.get(group(row))||0)+1));
    const categories=['全部',...groups.filter(g=>counts.has(g)),...[...counts.keys()].filter(g=>!groups.includes(g)).sort()];
    if(!categories.includes(ui.category))ui.category='全部';
    rows.sort((a,b)=>{const order=categories.indexOf(group(a))-categories.indexOf(group(b));return order||String(a.display_name||a.skill_name||a.name).localeCompare(String(b.display_name||b.skill_name||b.name),'zh');});
    const area=document.createElement('section');area.className='native-catalogue';area.setAttribute('aria-label',kind==='skills'?'能力分类目录':'算法分类目录');
    area.innerHTML=`<div class="catalogue-toolbar"><input type="search" aria-label="${kind==='skills'?'搜索能力':'搜索算法'}" placeholder="搜索名称、用途或标签"><span class="catalogue-count" role="status"></span></div><nav class="catalogue-categories" aria-label="${kind==='skills'?'能力分类':'算法分类'}"></nav><div class="native-public-grid"></div><p class="catalogue-empty" hidden>没有符合条件的条目。</p>`;
    shadow.insertBefore(area,container);container.classList.add('native-embedded-root');host.setAttribute('data-catalogue-ready','');
    const search=area.querySelector('input'),nav=area.querySelector('nav'),grid=area.querySelector('.native-public-grid');search.value=ui.query;
    const cards=rows.map(row=>{const card=document.createElement('article');card.className='panel native-public-card';card.dataset.category=group(row);const tag=document.createElement('span');tag.className='tag';tag.textContent=row.category||'未分类';const title=document.createElement('h2');title.textContent=row.display_name||row.skill_name||row.name;const description=document.createElement('p');description.textContent=row.description||'';const open=document.createElement('button');open.className='catalogue-open';open.textContent=kind==='skills'?'查看说明与资源 →':'查看与使用 →';open.onclick=()=>actions['native-skill-route']({dataset:{route:'/'+kind+'/'+row.id}}).catch(error=>notify(error.message));card.append(tag,title,description,open);grid.append(card);return {row,card,haystack:JSON.stringify(row).toLowerCase()};});
    function filter(){const q=ui.query.trim().toLowerCase();let total=0;for(const {card,haystack} of cards){const show=(ui.category==='全部'||card.dataset.category===ui.category)&&(!q||haystack.includes(q));card.hidden=!show;if(show)total++;}area.querySelector('.catalogue-count').textContent=total+' / '+rows.length+' 项';area.querySelector('.catalogue-empty').hidden=total>0;nav.querySelectorAll('button').forEach(b=>{b.classList.toggle('selected',b.dataset.category===ui.category);b.setAttribute('aria-pressed',b.dataset.category===ui.category);});}
    for(const category of categories){const button=document.createElement('button');button.type='button';button.dataset.category=category;button.textContent=category+' · '+(category==='全部'?rows.length:counts.get(category));button.onclick=()=>{ui.category=category;filter();};nav.append(button);}
    search.addEventListener('input',()=>{ui.query=search.value;filter();});filter();
  }

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
        .native-catalogue[hidden],.native-public-card[hidden],.catalogue-empty[hidden]{display:none!important}
        :host(:not([data-library-overview])) .native-catalogue{display:none}
        :host([data-library-overview][data-catalogue-ready]) .native-embedded-root{display:none}
        .catalogue-toolbar{display:flex;gap:18px;align-items:center;padding-right:42px}.catalogue-toolbar input{width:min(520px,100%);padding:12px 14px;border:1px solid #dce5d5;border-radius:10px;font:inherit;color:inherit;background:#fff}.catalogue-count{font-size:12px;color:#819078;white-space:nowrap}
        .catalogue-categories{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 22px}.catalogue-categories button{border:1px solid transparent;border-radius:9px;background:transparent;padding:9px 13px;font:inherit;color:#718367;cursor:pointer}.catalogue-categories button:hover{background:#f0f5e9}.catalogue-categories button.selected{background:#e5efda;border-color:#d5e3c4;color:#28563b}
        .native-catalogue .native-public-grid{margin-top:0}.catalogue-open{padding:0;border:0;background:transparent;color:#2e6748;font:inherit;cursor:pointer;text-align:left}.catalogue-empty{padding:30px 0;color:#829175}
        [data-legacy-launch]{display:flex;gap:8px;flex-wrap:wrap;max-width:100%}`;
      shadow.append(style);
      const container = document.createElement('div'); shadow.append(container);
      mounted = module.mountSkillCenter(container,{path:route,role:bioflowUser.role,csrf:()=>bioflowCsrf,onNavigate:url=>{updateRoute(url);if(bioflowUser?.role!=='developer'&&['/skills','/algorithms'].includes(url.split('?')[0])&&!root.querySelector('[data-native-skill-root]')?.hasAttribute('data-catalogue-ready'))render().catch(error=>notify(error.message));}});
      await publicCatalogue(host,shadow,container,ticket);
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
