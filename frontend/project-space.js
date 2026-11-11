/* Compact navigation; preserve the separately maintained Skills sidebar. */
(() => {
  let collapsed = localStorage.getItem('bioflow.projects.collapsed') === 'true';
  function syncLayout() {
    const ordinary = state.page !== 'skill-center';
    root.classList.toggle('project-space-layout', ordinary);
    root.classList.toggle('projects-collapsed', ordinary && collapsed);
    root.querySelectorAll('.rail .navitem').forEach(control => {
      const label = [...control.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join('').trim();
      if (label) { control.title = label; control.setAttribute('aria-label', label); }
    });
    let toggle = root.querySelector('[data-action="toggle-project-sidebar"]');
    if (!toggle) {
      toggle = document.createElement('button'); toggle.type = 'button';
      toggle.dataset.action = 'toggle-project-sidebar'; toggle.className = 'project-sidebar-toggle';
      root.querySelector('.topbar').prepend(toggle);
    }
    toggle.hidden = !ordinary;
    toggle.title = collapsed ? '展开项目栏' : '收起项目栏';
    toggle.setAttribute('aria-label', toggle.title);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.setAttribute('aria-controls', 'bioflow-project-sidebar');
    toggle.innerHTML = '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3" stroke="currentColor" stroke-width="1.6"/><path d="M9 4v16" stroke="currentColor" stroke-width="1.6"/><path d="'+(collapsed?'M13 9l3 3-3 3':'M16 9l-3 3 3 3')+'" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    root.querySelector('.shell>.projects').id = 'bioflow-project-sidebar';
  }
  const previousChrome = chrome;
  chrome = function () { previousChrome(); syncLayout(); };
  actions['toggle-project-sidebar'] = () => {
    collapsed = !collapsed;
    localStorage.setItem('bioflow.projects.collapsed', String(collapsed));
    syncLayout();
  };
  syncLayout();
})();
