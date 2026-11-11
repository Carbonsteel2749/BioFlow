/* Project navigation outside the independently maintained Skills workspace. */
(() => {
  let selected = '', query = '', returnFocus = null;
  const previousChrome = chrome;
  chrome = function () {
    root.classList.toggle('project-space-layout', state.page !== 'skill-center');
    previousChrome();
  };

  function rows() {
    const matches = state.projects.filter(p => `${p.name} ${p.description || ''}`.toLowerCase().includes(query.toLowerCase().trim()));
    return matches.map(p => `<div class="space-card ${p.id === selected ? 'selected' : ''}">
      <label class="space-choice"><input type="radio" name="project-space-choice" value="${esc(p.id)}" ${p.id === selected ? 'checked' : ''}>
      <span><strong>${esc(p.name)}</strong>${p.id === state.project ? '<small>当前空间</small>' : ''}<span class="space-description">${esc(p.description || '研究工作空间')}</span></span></label>
      <div class="space-manage">${button('编辑', 'project-edit', `data-id="${esc(p.id)}"`)}${button('删除', 'project-delete', `data-id="${esc(p.id)}"`)}</div></div>`).join('') || '<p class="space-empty">' + (state.projects.length ? '没有匹配的项目' : '暂无项目，点击“新建项目”开始。') + '</p>';
  }

  function showSpace() {
    selected = state.project; query = ''; returnFocus = document.activeElement;
    modal('项目空间', `<div class="space-account"><span>${esc(bioflowUser.username)}</span><div>${button('修改密码', 'password')}${button('退出登录', 'signout')}</div></div>
      <div class="space-toolbar"><input data-space-search aria-label="搜索项目空间" placeholder="搜索项目名称或简介">${button('＋ 新建项目', 'newproject')}</div>
      <div class="space-list" role="group" aria-label="选择项目空间">${rows()}</div>
      <div class="space-footer"><span data-space-selection></span>${button('切换项目空间', 'space-switch', 'class="primary"')}</div>`);
    const dialog = $('.overlay .modal');
    dialog.classList.add('project-space-dialog'); dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true'); dialog.setAttribute('aria-label', '项目空间');
    updateSelection(); dialog.querySelector('[data-space-search]').focus();
  }

  function updateSelection() {
    const p = state.projects.find(p => p.id === selected);
    $('[data-space-selection]').textContent = p ? '已选择：' + p.name : '请选择项目';
    $('[data-action="space-switch"]').disabled = !p;
  }

  // Runs before the existing document account handler without changing auth or Skills code.
  window.addEventListener('click', event => {
    const control = event.target.closest?.('.avatar,[data-action="account"]');
    if (!control || !root.contains(control) || !bioflowUser) return;
    event.preventDefault(); event.stopImmediatePropagation(); showSpace();
  }, true);

  root.addEventListener('input', event => {
    if (!event.target.matches('[data-space-search]')) return;
    query = event.target.value; $('.space-list').innerHTML = rows();
  });
  root.addEventListener('change', event => {
    if (event.target.name !== 'project-space-choice') return;
    selected = event.target.value;
    root.querySelectorAll('.space-card').forEach(card => card.classList.toggle('selected', card.querySelector('input').value === selected));
    updateSelection();
  });
  actions['space-switch'] = async () => {
    if (!state.projects.some(p => p.id === selected)) return;
    state.project = selected; state.task = ''; state.session = ''; state.chapter = ''; state.manifest = '';
    state.page = 'overview';
    localStorage.setItem('bioflow.project', selected);
    localStorage.setItem('bioflow.project.' + bioflowUser.id, selected);
    $('.overlay').hidden = true;
    await render();
  };
  root.addEventListener('keydown', event => {
    const dialog = event.target.closest('.project-space-dialog'); if (!dialog) return;
    if (event.key === 'Escape') { $('.overlay').hidden = true; returnFocus?.focus(); return; }
    if (event.key !== 'Tab') return;
    const controls = [...dialog.querySelectorAll('button:not(:disabled),input')];
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && event.target === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && event.target === last) { event.preventDefault(); first.focus(); }
  });
  root.classList.toggle('project-space-layout', state.page !== 'skill-center');
})();
