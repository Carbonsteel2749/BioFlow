/* Owned typed import form. Uploading continues in the existing upload queue. */
(function () {
  'use strict';

  const M = window.BioFlowInputs,
    states = new Map();
  let epoch = 0,
    timer,
    pending,
    recoveryReview;
  const key = () => JSON.stringify([bioflowUser?.id, state.project]);
  const pendingPrefix = 'bioflow-input-pending:';
  function marker(k, value) {
    // Persist only an opaque random request identifier; never source
    // references, biological rows, declarations or credentials.
    try {
      const name = pendingPrefix + k;
      if (value === null) localStorage.removeItem(name);
      else if (value !== undefined) localStorage.setItem(name, value);
      else {
        const stored = localStorage.getItem(name);
        if (stored?.length === 64 && /^[a-f0-9]{64}$/.test(stored)) return stored;
        if (stored) localStorage.removeItem(name);
      }
    } catch {}
    return null;
  }
  const get = () => {
    if (!states.has(key())) states.set(key(), {
      draft: M.draft(),
      candidates: [],
      next: null,
      catalog: null,
      storage: {},
      page: 0,
      filter: '',
      message: '',
      history: [],
      historyLimit: 20,
      recovery: marker(key()),
      job: null
    });
    return states.get(key());
  };
  const url = s => pa('/input-datasets' + s),
    active = (k, s, e) => k === key() && s === get() && e === epoch && state.page === 'input-datasets' && !!bioflowUser;
  const safeError = e => {
    let value = e?.message || '';
    try {
      const x = JSON.parse(value);
      value = typeof x === 'string' ? x : x.message || '';
    } catch {}
    return !value || /[\\/](?:home|runtime|Users|var|tmp)|Traceback/i.test(value) ? '服务暂不可用，请稍后重试；未判定为无数据。' : value.slice(0, 512);
  };
  const bytes = n => Number(n || 0) < 1024 ** 2 ? Number(n || 0).toLocaleString('zh-CN') + ' bytes' : (Number(n || 0) / 1024 ** 3).toLocaleString('zh-CN', {
    maximumFractionDigits: 3
  }) + ' GiB';
  const estimateText = s => '所选唯一来源最低新增复制量：' + bytes(M.selectedBytes(s.draft, s.candidates)) + ' · 存储保留空间 ' + bytes(s.storage.reserve_bytes) + '。转换、执行副本与完整归档另占空间；不是配额或保证上限。';
  const field = (label, body) => `<label class="wb-field"><span>${label}</span>${body}</label>`;
  const text = (key, value, extra = '') => `<input data-input-field="${key}" value="${esc(value)}" ${extra}>`;
  function source(s, value, attrs = '') {
    const formats = s.catalog?.formats?.[s.draft.kind] || [],
      matches = s.candidates.filter(c => formats.some(f => c.name.toLowerCase().endsWith(f)) && (!s.filter || [c.name, c.source_task, c.node].join(' ').toLowerCase().includes(s.filter.toLowerCase()))).slice(0, 50);
    const selected = s.candidates.find(c => c.id === value);
    if (selected && !matches.some(c => c.id === value)) matches.unshift(selected);
    return `<select ${attrs}><option value="">选择已上传文件 / 已登记产物</option>${matches.map(c => `<option value="${esc(c.id)}" ${c.id === value ? 'selected' : ''}>${esc(c.name)}${c.source_task ? ' · ' + esc(c.source_task) + ' / ' + esc(c.node || '节点未记录') : ''}</option>`).join('')}</select>`;
  }
  async function candidates(s, more = false) {
    const k = key(),
      e = epoch,
      request = (s.candidateRequest || 0) + 1;
    s.candidateRequest = request;
    try {
      const query = more ? s.next : null,
        reply = await api(url('/candidates') + (query ? '?' + new URLSearchParams(query) : ''));
      if (!active(k, s, e) || request !== s.candidateRequest) return;
      const map = new Map((more ? s.candidates : []).map(c => [c.id, c]));
      for (const c of reply.items || []) map.set(c.id, c);
      s.candidates = [...map.values()];
      s.next = reply.has_more ? reply.next : null;
      s.catalog = reply.catalog;
      s.storage = reply.storage || {};
      s.message = '';
    } catch (error) {
      if (active(k, s, e) && request === s.candidateRequest) s.message = safeError(error);
    }
  }
  async function history(s) {
    const k = key(), e = epoch, request = (s.historyRequest || 0) + 1;
    s.historyRequest = request;
    try {
      const reply = await api(url(''));
      if (!active(k, s, e) || request !== s.historyRequest) return;
      s.history = (reply.items || []).filter(j => typeof j.id === 'string');
      s.historyEpoch = e;
      s.historyError = '';
      if (s.recovery && marker(k) === s.recovery) {
        const found = s.history.find(j => j.request_id === s.recovery);
        if (found) {
          s.job = found;
          s.detail = null;
          s.recovery = null;
          s.uncertain = false;
          marker(k, null);
          s.message = '已找回先前受理的导入；无需重复提交。请选择刷新导入状态读取最新结果。';
        }
      }
      return true;
    } catch (error) {
      if (active(k, s, e) && request === s.historyRequest) s.historyError = safeError(error);
    }
  }
  function stop() {
    clearTimeout(timer);
    timer = null;
  }
  async function poll() {
    stop();
    if (state.page !== 'input-datasets' || document.hidden || !bioflowUser) return;
    const s = get();
    if (!s.job || !['queued', 'validating', 'ready'].includes(s.job.status)) return;
    const k = key(),
      e = epoch,
      request = (s.pollRequest || 0) + 1;
    const jobId = s.job.id;
    s.pollRequest = request;
    const isCurrent = () => active(k, s, e) && request === s.pollRequest && s.job?.id === jobId;
    try {
      if (s.job.status !== 'ready') {
        const reply = await api(url('/imports/' + id(jobId)));
        if (!isCurrent()) return;
        s.job = reply;
      }
      if (s.job.status === 'ready') {
        const detail = await api(url('/' + id(s.job.dataset_id)));
        if (!isCurrent()) return;
        s.detail = detail;
      }
      s.history = s.history.map(j => j.id === s.job.id ? s.job : j);
      if (!isCurrent()) return;
      s.message = '';
      await render();
    } catch (error) {
      if (isCurrent()) {
        s.message = safeError(error);
        await render();
      }
    }
    if (isCurrent() && ['queued', 'validating'].includes(s.job.status)) {
      timer = setTimeout(poll, 3000);
    }
  }
  names['input-datasets'] = '导入分析数据';
  actions['input-datasets'] = () => navigate('input-datasets');
  views['input-datasets'] = async () => {
    const s = get(), k = key(), e = epoch;
    if (!s.catalog) await candidates(s);
    if (!active(k, s, e)) return '';
    if (s.historyEpoch !== e) await history(s);
    if (!active(k, s, e)) return '';
    stop();
    if (!document.hidden && ['queued', 'validating'].includes(s.job?.status)) timer = setTimeout(poll, 3000);
    return html(s);
  };
  function html(s) {
    const d = s.draft,
      rows = d.kind === 'mags' ? d.mags : d.samples,
      start = s.page * 20,
      visible = rows.slice(start, start + 20),
      processed = d.kind === 'reads' && d.processing_status !== 'raw';
    const options = (values, current) => values.map(v => `<option value="${esc(v)}" ${v === current ? 'selected' : ''}>${esc(v)}</option>`).join('');
    let fields = '';
    if (d.kind === 'reads') fields += field('处理阶段', `<select data-input-field="processing_status">${options(s.catalog?.read_states || [], d.processing_status)}</select>`) + (processed ? field('外部处理说明（必填）', `<textarea data-input-field="processing_note">${esc(d.processing_note)}</textarea>`) + '<p class="wb-callout">用户声明、未经平台验证。文件名、来源任务与样本提示不证明处理阶段。</p>' : '');
    if (['contigs', 'mags'].includes(d.kind)) fields += field('队列 ID', text('cohort_id', d.cohort_id)) + '<p>队列来源由你负责确认；MAG 不专属于一个样本。分箱需要匹配的 clean / host_removed 双端 reads；不支持外部 MAG 定量。</p>';
    if (['reads', 'mags'].includes(d.kind)) fields += `<div class="input-rows">${visible.map((r, i) => {
      const index = start + i;
      return `<div class="input-row" data-row="${index}">${field(d.kind === 'reads' ? '样本 ID' : 'MAG ID', `<input data-input-row="${d.kind === 'reads' ? 'sample_id' : 'mag_id'}" value="${esc(r.sample_id ?? r.mag_id)}">`)}${d.kind === 'reads' ? field('R1', source(s, r.read1, 'data-input-row="read1"')) + field('R2', source(s, r.read2, 'data-input-row="read2"')) : field('核苷酸 FASTA', source(s, r.fasta, 'data-input-row="fasta"'))}${button('移除此行', 'input-remove', `type="button" data-index="${index}"`)}</div>`;
    }).join('')}</div><p>共 ${rows.length} 行 · 当前显示 ${start + 1}–${Math.min(start + 20, rows.length)}；提交校验全部行。</p><div class="row">${s.page ? button('上一组', 'input-row-page', 'type="button" data-delta="-1"') : ''}${start + 20 < rows.length ? button('下一组', 'input-row-page', 'type="button" data-delta="1"') : ''}${button('添加一行', 'input-add', 'type="button"')}</div>`;
    if (d.kind === 'contigs') fields += field('核苷酸 FASTA', source(s, d.fasta, 'data-input-field="fasta"'));
    if (d.kind === 'matrix') fields += field('CSV / TSV 矩阵', source(s, d.table, 'data-input-field="table"'))
      + field('特征类型', `<select data-input-field="feature_type">${options(s.catalog?.feature_types || [], d.feature_type)}</select>`)
      + field('单位（必须明确选择）', `<select data-input-field="unit"><option value="">请选择单位，不自动推断</option>${options(s.catalog?.units || [], d.unit)}</select>`)
      + field('标准化说明（必填）', text('normalization', d.normalization)) 
      + `<p>物种标准表：fraction / none；功能标准表：HUMAnN_reported / HUMAnN raw output；通路覆盖度须明确选 coverage。canonical 长表要求 sample_id,feature_id,value；未知宽表和原始 HUMAnN 两列表请先准备长表。不推断比例、不新增标准化。</p>${button('采用来源建议', 'input-hints', 'type="button"')}<small>提示：${esc(JSON.stringify(s.candidates.find(c => c.id === d.table)?.hints || {}))}（不代表内容已验证）</small>`;
    const estimate = M.selectedBytes(d, s.candidates),
      status = {
        queued: '排队中',
        validating: '正在复制并校验',
        ready: '已完成登记',
        failed: '导入失败'
      };
    return `<section class="input-import">
      <div class="wb-heading">
      <div>
      <h1>导入分析数据</h1>
      <p>登记已有文件的类型与处理阶段，再逐节点绑定到分析流程。</p>
      
      </div>
      ${button('返回分析', 'analysis')}</div>
      
      <div class="row">${button('打开现有上传队列', 'assets')}
      ${button('刷新来源', 'input-refresh')}${s.next ? button('加载更多来源', 'input-more') : ''}</div>
      
      <p>已加载 ${s.candidates.length} 个可选来源（不是全部项目文件）。${s.next ? '仍有下一页，即使本页无可用项也可继续加载。' : ''}</p>
      <section class="input-history" aria-label="项目导入记录">
      <h2>项目导入记录</h2>
      <p>仅显示当前账户、当前项目的持久化导入。返回后可选择记录继续查看；不会自动重新提交。</p>
      ${button('刷新导入记录', 'input-history-refresh')}
      ${s.historyError ? `<p role="alert">记录读取失败，不能据此判定无导入：${esc(s.historyError)}</p>` : ''}
      ${s.recovery ? '<p role="alert">先前提交的网络结果仍待确认。请刷新导入记录找回已受理任务；确认前暂停新提交，以免重复复制。原来源内容未存储。</p>' + button('核对记录并重新开始', 'input-recovery-start', `type="button" ${s.recoveryBusy ? 'disabled' : ''}`) : ''}
      <ul>${s.history.slice(0, s.historyLimit).map(j => `<li>${button((status[j.status] || '状态待确认') + ' · ' + j.id, 'input-job', `type="button" data-job="${esc(j.id)}"`)} <small>${esc(j.updated_at || j.created_at || '')}</small>${j.status === 'failed' ? `<p>${esc(safeError({message:j.error || '导入失败，请选择记录查看'}))}</p>` : ''}</li>`).join('')}</ul>
      ${!s.history.length && !s.historyError ? '<p>暂无已加载的导入记录。</p>' : ''}
      ${s.history.length > s.historyLimit ? button('加载更多导入记录', 'input-history-more') : ''}
      </section>
      
      <form data-form="input-import">
      <div class="input-form-head">${field('输入类型', `<select data-input-field="kind">${[['reads', '双端 reads'], ['contigs', 'Contigs'], ['mags', 'MAG 集合'], ['matrix', '丰度矩阵']].map(([v, t]) => `<option value="${v}" ${d.kind === v ? 'selected' : ''}>${t}</option>`).join('')}</select>`)}${field('筛选已加载来源', `<input data-input-filter value="${esc(s.filter)}" placeholder="文件名、任务或节点；每个选择器最多显示 50 项">`)}</div>
      
      <p class="wb-muted">接受格式：${esc((s.catalog?.formats?.[d.kind] || []).join('、')) || '目录尚未读取，请刷新来源'}</p>
      ${fields}<p class="wb-callout" data-input-estimate>所选唯一来源最低新增复制量：${bytes(estimate)} · 存储保留空间 ${bytes(s.storage.reserve_bytes)}。转换、执行副本与完整归档另占空间；不是配额或保证上限。</p>
      
      <details>
      <summary>格式、数值与转换限制</summary>
      <p>识别精确 BioFlow 富表时保留完整原始证据；排除诊断行后排除分层行，仅保留 community_total，登记实际排除数量。不将分层与总量重复相加。coverage 不可加和，均值仅为已观察行描述，不补零。</p>
      
      <pre>${esc(JSON.stringify({
      caps: s.catalog?.validator_caps,
      numeric: s.catalog?.numeric_policy
    }, null, 2))}</pre>
      </details>
      <button class="primary" type="submit" ${s.busy || s.recovery || ['queued', 'validating'].includes(s.job?.status) ? 'disabled' : ''}>${s.uncertain ? '重试相同导入请求' : '校验并登记数据'}</button>
      </form>
      <div role="status" aria-live="polite" data-input-status>${esc(s.message)}</div>
      ${s.job ? `<section>
      <h2>${status[s.job.status] || '状态待确认'}</h2>
      <p>${esc(s.job.id)} · ${esc(s.job.error ? safeError({message:s.job.error}) : '不显示虚构进度百分比')}</p>
      ${button('刷新导入状态', 'input-poll')}${s.detail ? `<p>以下仅为导入时冻结元数据，不是当前文件完整性复检；执行前服务仍将完整校验。</p>
      
      <details>
      <summary>登记与来源证据（只读）</summary>
      <pre>${esc(JSON.stringify(M.evidence(s.detail), null, 2))}</pre>
      </details>` : ''}</section>` : ''}</section>`;
  }
  async function submit(confirmed = false) {
    const s = get(),
      k = key(),
      e = epoch,
      endpoint = url('');
    if (s.busy || s.recovery) return;
    let spec;
    try {
      spec = M.spec(s.draft, confirmed);
    } catch (error) {
      s.message = error.message;
      await render();
      return;
    }
    if (spec.kind === 'reads' && spec.processing_status !== 'raw' && !confirmed) {
      pending = {
        k,
        s,
        e
      };
      const dialog = document.createElement('dialog');
      dialog.className = 'wb-submit-review';
      dialog.dataset.inputReview = '';
      dialog.setAttribute('aria-label', '核对外部处理声明');
      dialog.innerHTML = `<h2>用户声明、未经平台验证</h2><p>${esc(spec.processing_note)}</p><p>${spec.samples.length} 个样本 · ${esc(spec.processing_status)}。文件内容、配对和格式仍由后端校验。</p><ul>${spec.samples.slice(0, 20).map(r => `<li>${esc(r.sample_id)}</li>`).join('')}</ul>${spec.samples.length > 20 ? '<p>仅显示前 20 项，全部样本将提交。</p>' : ''}<div class="row">${button('返回修改', 'input-cancel', 'type="button"')}${button('确认声明并登记', 'input-confirm', 'type="button" class="primary"')}</div>`;
      root.append(dialog);
      dialog.addEventListener('cancel', () => {
        pending = null;
        dialog.remove();
      });
      dialog.showModal();
      return;
    }
    const serialized = JSON.stringify(spec);
    // Retire a failed attempt once, before its replacement POST. If that response
    // is lost, the replacement request remains the one uncertain request to retry.
    if (s.job?.status === 'failed') {
      s.request = null;
      s.job = null;
      s.detail = null;
    }
    if (!s.request || s.request.serialized !== serialized) {
      s.request = {
        serialized,
        body: {
          idempotency_key: 'typedimport_' + Array.from(crypto.getRandomValues(new Uint8Array(32)), value => value.toString(16).padStart(2, '0')).join(''),
          spec
        }
      };
    }
    s.busy = true;
    const request = s.request;
    const digest = request.body.idempotency_key.slice('typedimport_'.length);
    if (!active(k, s, e)) { s.busy = false; return; }
    marker(k, digest);
    s.message = '正在提交登记请求…';
    await render();
    if (!active(k, s, e)) {
      s.busy = false;
      if (marker(k) === digest) marker(k, null);
      return;
    }
    try {
      const job = await api(endpoint, 'POST', request.body);
      if (marker(k) === digest) marker(k, null);
      if (!active(k, s, e)) return;
      s.job = job;
      s.uncertain = false;
      s.detail = null;
      s.message = '已受理；校验完成后才可用于分析。';
      s.history = [job, ...s.history.filter(j => j.id !== job.id)];
    } catch (error) {
      if (active(k, s, e)) {
        s.uncertain = error.name === 'TypeError';
        if (!s.uncertain && marker(k) === digest) marker(k, null);
        s.message = (s.uncertain ? '网络结果不确定；保持原内容重试会复用同一幂等键。 ' : '') + safeError(error);
      }
    } finally {
      s.busy = false;
      if (active(k, s, e)) await render();
    }
  }
  forms['input-import'] = () => submit();
  actions['input-confirm'] = async () => {
    const p = pending;
    actions['input-cancel']();
    if (p && active(p.k, p.s, p.e)) await submit(true);
  };
  actions['input-cancel'] = () => {
    const d = $('[data-input-review]');
    if (d) {
      d.close();
      d.remove();
    }
    pending = null;
  };
  actions['input-refresh'] = async () => {
    await candidates(get());
    await render();
  };
  actions['input-more'] = async () => {
    if (get().next) await candidates(get(), true);
    await render();
  };
  actions['input-poll'] = poll;
  actions['input-recovery-cancel'] = () => {
    const dialog = $('[data-input-recovery-review]');
    if (dialog) { dialog.close(); dialog.remove(); }
    recoveryReview = null;
  };
  async function recover(p, confirmed) {
    const {k, s, e, digest} = p;
    const current = () => active(k, s, e) && s.recovery === digest && marker(k) === digest;
    if (!digest || !current() || s.recoveryBusy) return;
    s.recoveryBusy = true;
    const reconciled = await history(s);
    s.recoveryBusy = false;
    if (!active(k, s, e)) return;
    if (!reconciled || !current()) { await render(); return; }
    if (confirmed) {
      // This retires only the browser gate, not any server job. Absence from
      // history is not proof of nonacceptance; the user accepts duplicate risk.
      marker(k, null);
      s.recovery = null;
      s.request = null;
      s.uncertain = false;
      s.message = '已解除本账户、本项目的待确认标记；未取消任何服务端任务，也未重新提交。请核对来源后手动登记，仍可能重复导入。';
      await render();
      return;
    }
    await render();
    if (!current()) return;
    recoveryReview = p;
    const dialog = document.createElement('dialog');
    dialog.className = 'wb-submit-review';
    dialog.dataset.inputRecoveryReview = '';
    dialog.setAttribute('aria-label', '确认重新开始导入');
    dialog.innerHTML = `<h2>仍可能重复导入</h2><p>刚刚读取的记录中未找到匹配任务，但这不能证明先前请求未被受理。重新开始可能重复复制文件、占用存储。</p><p>确认仅解除本账户、本项目的待确认标记；不会取消或删除任何服务端任务，也不会自动提交。确认时将再次核对记录，若找回任务则直接选择它。</p><div class="row">${button('保留标记，继续等待', 'input-recovery-cancel', 'type="button" autofocus')}${button('接受重复风险，解除标记', 'input-recovery-confirm', 'type="button" class="primary"')}</div>`;
    root.append(dialog);
    dialog.addEventListener('cancel', () => { recoveryReview = null; dialog.remove(); });
    dialog.showModal();
  }
  actions['input-recovery-start'] = async () => {
    if (recoveryReview) return;
    const s = get();
    await recover({k: key(), s, e: epoch, digest: s.recovery}, false);
  };
  actions['input-recovery-confirm'] = async () => {
    const p = recoveryReview;
    actions['input-recovery-cancel']();
    if (p) await recover(p, true);
  };
  actions['input-history-refresh'] = async () => { await history(get()); await render(); };
  actions['input-history-more'] = async () => { get().historyLimit += 20; await render(); };
  actions['input-job'] = async el => {
    const s = get(), job = s.history.find(j => j.id === el.dataset.job);
    if (!job) return;
    stop();
    s.pollRequest = (s.pollRequest || 0) + 1;
    s.job = job;
    s.detail = null;
    s.message = '';
    await poll();
    await render();
  };
  actions['input-add'] = async () => {
    const s = get(),
      m = s.draft.kind === 'mags',
      rows = m ? s.draft.mags : s.draft.samples;
    if (rows.length >= (m ? 10000 : 1024)) {
      s.message = '已达到服务登记行数上限';
    } else {
      rows.push(m ? {
        mag_id: '',
        fasta: ''
      } : {
        sample_id: '',
        read1: '',
        read2: ''
      });
      s.page = Math.floor((rows.length - 1) / 20);
    }
    await render();
  };
  actions['input-remove'] = async el => {
    const s = get(),
      rows = s.draft.kind === 'mags' ? s.draft.mags : s.draft.samples;
    rows.splice(Number(el.dataset.index), 1);
    s.page = Math.min(s.page, Math.max(0, Math.ceil(rows.length / 20) - 1));
    await render();
  };
  actions['input-row-page'] = async el => {
    get().page += Number(el.dataset.delta);
    await render();
  };
  actions['input-hints'] = async () => {
    const s = get(),
      h = s.candidates.find(c => c.id === s.draft.table)?.hints;
    if (h) {
      for (const k of ['feature_type', 'unit', 'normalization']) if (h[k]) s.draft[k] = h[k];
      s.message = '已按你的操作采用来源建议；服务仍将验证格式和语义。';
    } else s.message = '此来源没有可采用的语义建议，请明确填写。';
    await render();
  };
  root.addEventListener('input', e => {
    const el = e.target,
      s = get();
    if (el.dataset.inputField) s.draft[el.dataset.inputField] = el.value;
    if (el.dataset.inputRow) {
      const rows = s.draft.kind === 'mags' ? s.draft.mags : s.draft.samples;
      rows[Number(el.closest('[data-row]').dataset.row)][el.dataset.inputRow] = el.value;
    }
    if (el.matches('[data-input-filter]')) s.filter = el.value;
    const estimate = $('[data-input-estimate]');
    if (estimate) estimate.textContent = estimateText(s);
  });
  root.addEventListener('change', async e => {
    if (e.target.matches('[data-input-field=kind]')) {
      get().page = 0;
      await render();
    } else if (e.target.matches('[data-input-filter],[data-input-field=processing_status],[data-input-field=table]')) await render();
  });
  const oldChrome = chrome;
  chrome = function () {
    oldChrome();
    if (state.page !== 'input-datasets') {
      stop();
      ++epoch;
      actions['input-cancel']();
      actions['input-recovery-cancel']();
    }
  };
  document.addEventListener('visibilitychange', () => {
    stop();
    ++epoch;
    actions['input-cancel']();
    actions['input-recovery-cancel']();
    if (!document.hidden) poll();
  });
  const oldSignout = actions.signout;
  actions.signout = async () => {
    stop();
    ++epoch;
    actions['input-cancel']();
    actions['input-recovery-cancel']();
    states.clear();
    try {
      for (let i = localStorage.length - 1; i >= 0; --i) {
        const name = localStorage.key(i);
        if (!name?.startsWith(pendingPrefix)) continue;
        try { if (JSON.parse(name.slice(pendingPrefix.length))[0] === bioflowUser?.id) localStorage.removeItem(name); } catch {}
      }
    } catch {}
    await oldSignout();
  };
})();
