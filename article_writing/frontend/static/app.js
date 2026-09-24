(() => {
  const CHAPTERS = [
    { id: "abstract", title: "Abstract", title_zh: "摘要" },
    { id: "introduction", title: "Introduction", title_zh: "引言" },
    { id: "methods", title: "Methods", title_zh: "方法" },
    { id: "results", title: "Results", title_zh: "结果" },
    { id: "discussion", title: "Discussion", title_zh: "讨论" },
    { id: "conclusion", title: "Conclusions", title_zh: "结论" },
    { id: "back_matter", title: "Back Matter", title_zh: "文后" },
  ];
  const VIEWS = {
    overview: { title: "工作台", sub: "连接状态、当前稿件与最近会话" },
    sources: { title: "素材接入", sub: "课题简介 · BioLLM 结果 · 文献检索 · 写作 Skill" },
    draft: { title: "分章撰写", sub: "编辑 Markdown，对话润色，逐章确认" },
    export: { title: "排版导出", sub: "全部确认后生成 Markdown / Word / PDF" },
  };
  const SOURCE_KEYS = [
    "opt-brief-path",
    "opt-analysis-url",
    "opt-analysis-task",
    "opt-analysis-dir",
    "opt-analysis-bundle",
    "opt-query",
    "opt-literature-url",
    "opt-literature-db",
  ];
  const FIXTURE_BRIEF = "/home/xh/BioFLow/article_writing/fixtures/biollm_metagenome/paper_brief.json";
  const FIXTURE_PACKAGE = "/home/xh/BioFLow/article_writing/fixtures/biollm_metagenome/package";

  const state = {
    view: "overview",
    session: null,
    currentSection: null,
    dirty: false,
    connections: null,
  };

  const $ = (id) => document.getElementById(id);

  const toast = (msg, isError = false) => {
    const el = $("toast");
    el.hidden = false;
    el.textContent = msg;
    el.classList.toggle("error", isError);
    clearTimeout(toast._t);
    toast._t = setTimeout(() => {
      el.hidden = true;
    }, 4200);
  };

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail || res.statusText || "request failed";
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function persistSources() {
    const payload = {};
    SOURCE_KEYS.forEach((id) => {
      const el = $(id);
      if (el) payload[id] = el.value;
    });
    payload["opt-live-lit"] = $("opt-live-lit")?.checked || false;
    payload["opt-llm-start"] = $("opt-llm-start")?.checked || false;
    payload["opt-react"] = $("opt-react")?.checked !== false;
    ["opt-skill-writing", "opt-skill-polishing", "opt-skill-reviewer", "opt-skill-response"].forEach((id) => {
      payload[id] = $(id)?.checked || false;
    });
    payload["opt-editor-letter"] = $("opt-editor-letter")?.value || "";
    payload["opt-reviewer-comments"] = $("opt-reviewer-comments")?.value || "";
    localStorage.setItem("aw-sources", JSON.stringify(payload));
  }

  function restoreSources() {
    try {
      const payload = JSON.parse(localStorage.getItem("aw-sources") || "{}");
      SOURCE_KEYS.forEach((id) => {
        if (payload[id] && $(id)) $(id).value = payload[id];
      });
      if ($("opt-live-lit")) $("opt-live-lit").checked = !!payload["opt-live-lit"];
      if ($("opt-llm-start")) $("opt-llm-start").checked = !!payload["opt-llm-start"];
      if ($("opt-react") && payload["opt-react"] === false) $("opt-react").checked = false;
      ["opt-skill-writing", "opt-skill-polishing", "opt-skill-reviewer", "opt-skill-response"].forEach((id) => {
        if ($(id)) $(id).checked = !!payload[id];
      });
      if ($("opt-editor-letter") && payload["opt-editor-letter"]) {
        $("opt-editor-letter").value = payload["opt-editor-letter"];
      }
      if ($("opt-reviewer-comments") && payload["opt-reviewer-comments"]) {
        $("opt-reviewer-comments").value = payload["opt-reviewer-comments"];
      }
    } catch {
      /* ignore */
    }
    syncSkillResponseFields();
    if (!$("opt-analysis-url").value) $("opt-analysis-url").value = "http://127.0.0.1:8000";
    if (!$("opt-query").value) $("opt-query").value = "gut microbiota";
  }

  function sourcePayload() {
    persistSources();
    return {
      use_llm: $("opt-llm-start").checked,
      analysis_run_dir: $("opt-analysis-dir").value.trim(),
      analysis_bundle: $("opt-analysis-bundle").value.trim(),
      brief_path: $("opt-brief-path").value.trim(),
      analysis_url: $("opt-analysis-url").value.trim(),
      analysis_task_id: $("opt-analysis-task").value.trim(),
      literature_query: $("opt-query").value.trim(),
      literature_url: $("opt-literature-url").value.trim(),
      literature_db: $("opt-literature-db").value.trim(),
      use_live_literature: $("opt-live-lit").checked,
      react_enabled: $("opt-react").checked,
      skills: selectedSkills(),
      editor_letter: $("opt-editor-letter")?.value.trim() || "",
      reviewer_comments: $("opt-reviewer-comments")?.value.trim() || "",
    };
  }

  function selectedSkills() {
    const map = [
      ["opt-skill-writing", "nature-writing"],
      ["opt-skill-polishing", "nature-polishing"],
      ["opt-skill-reviewer", "nature-reviewer"],
      ["opt-skill-response", "nature-response"],
    ];
    return map.filter(([id]) => $(id)?.checked).map(([, name]) => name);
  }

  function syncSkillResponseFields() {
    const box = $("skill-response-fields");
    if (!box) return;
    box.hidden = !$("opt-skill-response")?.checked;
  }

  function chapterMeta(sid) {
    return CHAPTERS.find((c) => c.id === sid) || { id: sid, title: sid, title_zh: sid };
  }

  function showView(name) {
    state.view = name;
    Object.keys(VIEWS).forEach((key) => {
      const el = $(`view-${key}`);
      if (el) el.hidden = key !== name;
    });
    document.querySelectorAll(".nav-item").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.view === name);
    });
    $("top-title").textContent = VIEWS[name].title;
    $("top-sub").textContent = VIEWS[name].sub;
    const chapterBlock = $("chapter-block");
    if (chapterBlock) chapterBlock.hidden = name !== "draft";
    location.hash = name;
    if (name === "overview") void refreshOverview();
    if (name === "export") renderExport();
  }

  function renderPills() {
    const host = $("conn-pills");
    host.innerHTML = "";
    const conns = state.connections || {};
    ["analysis", "literature", "ollama"].forEach((key) => {
      const item = conns[key];
      if (!item) return;
      const span = document.createElement("span");
      span.className = `pill ${item.ok ? "on" : "off"}`;
      span.textContent = `${item.label} ${item.ok ? "在线" : "未开"}`;
      host.appendChild(span);
    });
  }

  function renderNav() {
    const nav = $("section-nav");
    nav.innerHTML = "";
    const order = state.session?.section_order?.length
      ? state.session.section_order
      : CHAPTERS.map((c) => c.id);
    order.forEach((sid, index) => {
      const meta = chapterMeta(sid);
      const sec = state.session?.sections?.[sid];
      const ready = Boolean(sec);
      const btn = document.createElement("button");
      btn.type = "button";
      if (!ready) btn.classList.add("pending");
      if (sid === state.currentSection && state.view === "draft") btn.classList.add("active");
      let badgeText = "待生成";
      let badgeClass = "badge";
      if (ready && sec.confirmed) {
        badgeText = "已确认";
        badgeClass = "badge ok";
      } else if (ready) {
        badgeText = "待确认";
        badgeClass = "badge wait";
      }
      btn.innerHTML = `
        <span class="idx">${index + 1}</span>
        <span class="label-wrap">
          <span class="label-zh">${meta.title_zh}</span>
          <span class="label-en">${meta.title}</span>
        </span>
        <span class="${badgeClass}">${badgeText}</span>`;
      btn.addEventListener("click", () => {
        if (!state.session || !state.session.sections[sid]) {
          toast("请先在「素材接入」生成初稿", true);
          showView("sources");
          return;
        }
        showView("draft");
        selectSection(sid);
      });
      nav.appendChild(btn);
    });
    if (!state.session) {
      $("progress").textContent = `尚未生成 · 共 ${order.length} 章`;
      $("btn-export").disabled = true;
      return;
    }
    $("progress").textContent = `${state.session.confirmed_count} / ${state.session.total_sections} 已确认`;
    $("btn-export").disabled = !state.session.all_confirmed;
  }

  function renderChat() {
    const log = $("chat-log");
    log.innerHTML = "";
    if (!state.session || !state.currentSection) return;
    const sec = state.session.sections[state.currentSection];
    (sec.chat || []).forEach((msg) => {
      const div = document.createElement("div");
      div.className = `bubble ${msg.role === "user" ? "user" : "assistant"}`;
      div.textContent = msg.content;
      log.appendChild(div);
    });
    log.scrollTop = log.scrollHeight;
  }

  function enableEditing(on) {
    $("editor").disabled = !on;
    $("chat-input").disabled = !on;
    $("btn-chat").disabled = !on;
    $("btn-save").disabled = !on;
    $("btn-confirm").disabled = !on;
  }

  function selectSection(sid) {
    if (state.dirty && !confirm("当前章节有未保存修改，切换将丢弃，继续？")) return;
    state.currentSection = sid;
    state.dirty = false;
    const meta = chapterMeta(sid);
    const sec = state.session.sections[sid];
    $("section-heading").textContent = `${meta.title_zh} / ${meta.title}`;
    $("editor").value = sec.markdown || "";
    $("btn-confirm").hidden = !!sec.confirmed;
    $("btn-unconfirm").hidden = !sec.confirmed;
    enableEditing(true);
    $("editor-status").textContent = sec.confirmed
      ? `已确认 · ${sec.markdown.length} 字`
      : `待确认 · ${sec.markdown.length} 字`;
    $("section-hint").textContent = "可直接改正文，或在右侧用本地 Qwen 改写。改完请保存并确认。";
    renderNav();
    renderChat();
  }

  function applySession(session) {
    state.session = session;
    localStorage.setItem("aw-session", session.session_id);
    $("paper-title").textContent = session.title || session.run_id;
    $("session-chip").textContent = session.title || session.session_id;
    enableEditing(true);
    const first = session.section_order[0];
    state.currentSection = first;
    state.dirty = false;
    selectSection(first);
    renderExport();
  }

  // ── LLM 模型设置 ─────────────────────────────────────────────────────────

  function renderLlmStatus(payload) {
    const config = payload?.config || {};
    const available = payload?.available_models || [];
    const status = $("llm-status");
    const label = $("chat-llm-label");

    const options = $("llm-model-options");
    if (options) {
      options.innerHTML = "";
      available.forEach((name) => {
        const option = document.createElement("option");
        option.value = name;
        options.appendChild(option);
      });
    }
    if (label) label.textContent = `${config.provider || "ollama"} · ${config.model || "未设置"}`;
    if (!status) return;

    const ok = payload?.model_available;
    status.classList.toggle("bad", ok === false);
    if (ok === false) {
      status.textContent = payload?.warning || "模型不可用";
    } else if (ok === true) {
      status.textContent = `模型可用：${config.model}（已安装 ${available.length} 个）`;
    } else {
      status.textContent = payload?.warning || `已配置 ${config.provider} / ${config.model}`;
    }
  }

  async function loadLlmConfig() {
    try {
      const payload = await api("/api/llm-config");
      const config = payload.config || {};
      if ($("llm-provider")) $("llm-provider").value = config.provider || "ollama";
      if ($("llm-model")) $("llm-model").value = config.model || "";
      if ($("llm-url")) $("llm-url").value = config.url || "";
      if ($("llm-timeout")) $("llm-timeout").value = config.timeout || 300;
      renderLlmStatus(payload);
      if (payload.model_available === false && payload.warning) toast(payload.warning, true);
    } catch (err) {
      if ($("llm-status")) {
        $("llm-status").textContent = `读取模型配置失败：${err.message}`;
        $("llm-status").classList.add("bad");
      }
    }
  }

  $("btn-llm-save")?.addEventListener("click", async () => {
    try {
      setBusy(true, "保存模型设置…");
      const payload = await api("/api/llm-config", {
        method: "PUT",
        body: JSON.stringify({
          provider: $("llm-provider").value,
          model: $("llm-model").value.trim(),
          url: $("llm-url").value.trim(),
          timeout: Number($("llm-timeout").value) || 300,
        }),
      });
      renderLlmStatus(payload);
      const bad = payload.model_available === false;
      toast(bad ? `已保存，但模型不可用：${payload.warning || ""}` : "模型设置已保存", bad);
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
    }
  });

  async function refreshOverview() {
    try {
      const conns = await api("/api/connections");
      state.connections = conns;
      renderPills();
      const host = $("ov-conns");
      host.innerHTML = "";
      Object.entries(conns).forEach(([key, item]) => {
        if (!item || typeof item !== "object" || !item.label) return;
        const card = document.createElement("div");
        card.className = "conn-card";
        card.innerHTML = `<b>${item.label}</b><span>${item.ok ? "可用" : "离线"} · ${item.url || item.path || key}</span>`;
        host.appendChild(card);
      });
    } catch (err) {
      toast(err.message, true);
    }
    if (state.session) {
      $("ov-title").textContent = state.session.title || "未命名稿件";
      $("ov-question").textContent = state.session.research_question || "无研究问题";
      $("ov-stats").innerHTML = `
        <div><dt>已确认</dt><dd>${state.session.confirmed_count}/${state.session.total_sections}</dd></div>
        <div><dt>模式</dt><dd>${state.session.source?.adapter_mode || "—"}</dd></div>`;
    }
    try {
      const data = await api("/api/sessions");
      const box = $("ov-sessions");
      if (!data.sessions?.length) {
        box.className = "session-list muted";
        box.textContent = "暂无本地会话";
        return;
      }
      box.className = "session-list";
      box.innerHTML = "";
      data.sessions.slice(0, 6).forEach((item) => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "session-card";
        card.innerHTML = `<b>${item.title || item.session_id}</b><span>${item.confirmed_count}/${item.total_sections} 已确认 · ${item.session_id}</span>`;
        card.addEventListener("click", async () => {
          const session = await api(`/api/sessions/${item.session_id}`);
          applySession(session);
          showView("draft");
        });
        box.appendChild(card);
      });
    } catch {
      /* ignore */
    }
  }

  function renderExport() {
    const list = $("export-checklist");
    const warns = $("export-warnings");
    if (!state.session) {
      list.textContent = "尚未生成";
      return;
    }
    list.innerHTML = "";
    (state.session.section_order || []).forEach((sid) => {
      const sec = state.session.sections[sid];
      const meta = chapterMeta(sid);
      const row = document.createElement("div");
      row.className = "task-row";
      row.innerHTML = `<b>${meta.title_zh}</b><span>${sec?.confirmed ? "已确认" : "未确认"} · ${(sec?.markdown || "").length} 字</span>`;
      list.appendChild(row);
    });
    warns.innerHTML = "";
    const warnings = state.session.warnings || [];
    if (!warnings.length) {
      warns.innerHTML = "<li>暂无流水线警告</li>";
    } else {
      warnings.slice(0, 12).forEach((text) => {
        const li = document.createElement("li");
        li.textContent = text;
        warns.appendChild(li);
      });
    }
    $("btn-export").disabled = !state.session.all_confirmed;
    if (state.session.manuscript) $("export-preview").textContent = state.session.manuscript;
    if (state.session.exported_path) $("export-path").textContent = state.session.exported_path;
  }

  function setBusy(busy, label) {
    ["btn-start", "btn-save", "btn-confirm", "btn-unconfirm", "btn-export", "btn-chat", "btn-demo"].forEach((id) => {
      const el = $(id);
      if (!el) return;
      if (busy) el.dataset.prevDisabled = el.disabled ? "1" : "0";
      if (busy) el.disabled = true;
      else if (el.dataset.prevDisabled === "0") el.disabled = false;
    });
    if (label) $("editor-status").textContent = label;
    if (label) $("gen-status").textContent = label;
  }

  async function generate(extra = {}) {
    try {
      setBusy(true, "正在生成各章初稿…");
      toast("正在生成分章初稿，请稍候");
      const session = await api("/api/sessions", {
        method: "POST",
        body: JSON.stringify({ ...sourcePayload(), ...extra }),
      });
      applySession(session);
      showView("draft");
      toast("初稿已就绪，可分章编辑");
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false, "可再次生成；会新建会话而不是覆盖当前稿。");
      renderNav();
    }
  }

  $("editor").addEventListener("input", () => {
    state.dirty = true;
    $("editor-status").textContent = "有未保存修改…";
  });

  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.addEventListener("click", () => showView(btn.dataset.view));
  });
  document.querySelectorAll("[data-go]").forEach((btn) => {
    btn.addEventListener("click", () => showView(btn.dataset.go));
  });

  $("btn-fill-brief").addEventListener("click", () => {
    $("opt-brief-path").value = FIXTURE_BRIEF;
    persistSources();
  });
  $("btn-fill-package").addEventListener("click", () => {
    $("opt-analysis-dir").value = FIXTURE_PACKAGE;
    $("opt-brief-path").value = FIXTURE_BRIEF;
    persistSources();
    toast("已填入宏基因组夹具路径");
  });
  $("btn-demo").addEventListener("click", () => {
    $("opt-analysis-dir").value = FIXTURE_PACKAGE;
    $("opt-brief-path").value = FIXTURE_BRIEF;
    $("opt-query").value = "gut microbiota";
    persistSources();
    void generate();
  });

  $("btn-load-tasks").addEventListener("click", async () => {
    try {
      const base = $("opt-analysis-url").value.trim() || "http://127.0.0.1:8000";
      const data = await api(`/api/upstream/analysis/tasks?base_url=${encodeURIComponent(base)}`);
      const box = $("task-list");
      box.innerHTML = "";
      const tasks = data.tasks || [];
      if (!tasks.length) {
        box.textContent = "没有任务，或分析服务未开。";
        return;
      }
      tasks.slice(0, 8).forEach((task) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "task-row";
        row.innerHTML = `<b>${task.id}</b><span>${task.status || ""} ${task.result_archive ? "· 可下载结果包" : ""}</span>`;
        row.addEventListener("click", () => {
          $("opt-analysis-task").value = task.id;
          $("opt-analysis-url").value = base;
          persistSources();
          toast(`已选任务 ${task.id}`);
        });
        box.appendChild(row);
      });
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("btn-preview-task").addEventListener("click", async () => {
    const taskId = $("opt-analysis-task").value.trim();
    if (!taskId) {
      toast("先填任务 ID 或从列表点选", true);
      return;
    }
    try {
      const base = $("opt-analysis-url").value.trim() || "http://127.0.0.1:8000";
      const data = await api(
        `/api/upstream/analysis/preview?task_id=${encodeURIComponent(taskId)}&base_url=${encodeURIComponent(base)}`
      );
      const preview = $("analysis-preview");
      preview.hidden = false;
      preview.textContent = JSON.stringify(data.preview, null, 2).slice(0, 4000);
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("btn-preview-lit").addEventListener("click", async () => {
    const query = $("opt-query").value.trim();
    if (!query) {
      toast("先填检索词", true);
      return;
    }
    try {
      const params = new URLSearchParams({
        query,
        use_live: $("opt-live-lit").checked ? "true" : "false",
        literature_url: $("opt-literature-url").value.trim(),
        literature_db: $("opt-literature-db").value.trim(),
      });
      const data = await api(`/api/literature/preview?${params}`);
      const box = $("lit-hits");
      box.innerHTML = "";
      (data.hits || []).forEach((hit) => {
        const row = document.createElement("div");
        row.className = "hit";
        row.innerHTML = `<b>${hit.title || hit.paper_id}</b><span>${hit.year || ""} ${hit.doi || ""} · ${data.source}</span>`;
        box.appendChild(row);
      });
      if (!data.hits?.length) box.textContent = "没有命中";
    } catch (err) {
      toast(err.message, true);
    }
  });

  $("btn-start").addEventListener("click", () => void generate());

  $("btn-save").addEventListener("click", async () => {
    if (!state.session || !state.currentSection) return;
    try {
      setBusy(true, "保存中…");
      const sec = await api(
        `/api/sessions/${state.session.session_id}/sections/${state.currentSection}`,
        { method: "PUT", body: JSON.stringify({ markdown: $("editor").value }) }
      );
      state.session.sections[state.currentSection] = sec;
      state.session.all_confirmed = Object.values(state.session.sections).every((s) => s.confirmed);
      state.session.confirmed_count = Object.values(state.session.sections).filter((s) => s.confirmed).length;
      state.dirty = false;
      $("btn-confirm").hidden = false;
      $("btn-unconfirm").hidden = true;
      toast("已保存（确认状态已重置）");
      $("editor-status").textContent = "已保存";
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
      renderNav();
    }
  });

  async function setConfirmed(confirmed) {
    if (!state.session || !state.currentSection) return;
    if (state.dirty) {
      toast("请先保存手写修改", true);
      return;
    }
    try {
      setBusy(true, confirmed ? "确认中…" : "取消确认…");
      const data = await api(
        `/api/sessions/${state.session.session_id}/sections/${state.currentSection}/confirm`,
        { method: "POST", body: JSON.stringify({ confirmed }) }
      );
      state.session.sections[state.currentSection] = data.section;
      state.session.all_confirmed = data.all_confirmed;
      state.session.confirmed_count = data.confirmed_count;
      state.session.total_sections = data.total_sections;
      $("btn-confirm").hidden = !!confirmed;
      $("btn-unconfirm").hidden = !confirmed;
      toast(confirmed ? "本章已确认" : "已取消确认");
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
      renderNav();
      renderExport();
    }
  }

  $("btn-confirm").addEventListener("click", () => setConfirmed(true));
  $("btn-unconfirm").addEventListener("click", () => setConfirmed(false));

  $("chat-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!state.session || !state.currentSection) return;
    const message = $("chat-input").value.trim();
    if (!message) return;
    try {
      setBusy(true, "大模型改写中…");
      const data = await api(
        `/api/sessions/${state.session.session_id}/sections/${state.currentSection}/chat`,
        { method: "POST", body: JSON.stringify({ message, use_llm: $("opt-llm-chat").checked }) }
      );
      state.session.sections[state.currentSection] = data.section;
      $("editor").value = data.section.markdown;
      state.dirty = false;
      $("chat-input").value = "";
      $("btn-confirm").hidden = false;
      $("btn-unconfirm").hidden = true;
      renderChat();
      if (data.applied) {
        const warns = data.warnings || [];
        // 成功但有告警（例如模型没有产出中英双语结构）也要显式提示，不静默通过
        toast(warns.length ? `改写已写回，但有告警：${String(warns[0]).slice(0, 90)}` : "改写已写回编辑器", warns.length > 0);
      } else {
        toast(data.error ? `未应用改写：${data.error}` : "未应用改写，见对话说明", true);
      }
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
      renderNav();
    }
  });

  $("btn-export").addEventListener("click", async () => {
    if (!state.session) return;
    try {
      setBusy(true, "统一排版中…");
      const data = await api(`/api/sessions/${state.session.session_id}/export`, {
        method: "POST",
        body: "{}",
      });
      state.session = data.session;
      $("export-path").textContent = data.exported_path;
      $("export-preview").textContent = data.manuscript;
      const sid = state.session.session_id;
      $("dl-md").href = data.download?.markdown || `/api/sessions/${sid}/download/markdown`;
      $("dl-docx").href = data.download?.docx || `/api/sessions/${sid}/download/docx`;
      $("dl-pdf").href = data.download?.pdf || `/api/sessions/${sid}/download/pdf`;
      $("dl-docx").setAttribute("aria-disabled", data.docx_path ? "false" : "true");
      $("dl-pdf").setAttribute("aria-disabled", data.pdf_path ? "false" : "true");
      $("export-hint").textContent =
        data.docx_path && data.pdf_path
          ? "已生成 Markdown / Word / PDF。"
          : [data.docx_path ? "Word 已生成。" : `Word：${data.docx_error || "未生成"}`, data.pdf_path ? "PDF 已生成。" : `PDF：${data.pdf_error || "请检查 LibreOffice"}`].join(" ");
      toast(data.docx_path && data.pdf_path ? "已导出三种格式" : "导出完成（部分格式可能失败）");
      renderExport();
    } catch (err) {
      toast(err.message, true);
    } finally {
      setBusy(false);
      renderNav();
    }
  });

  SOURCE_KEYS.forEach((id) => $(id)?.addEventListener("change", persistSources));
  ["opt-live-lit", "opt-llm-start", "opt-react", "opt-skill-writing", "opt-skill-polishing", "opt-skill-reviewer", "opt-skill-response"].forEach((id) => {
    $(id)?.addEventListener("change", () => {
      syncSkillResponseFields();
      persistSources();
    });
  });
  ["opt-editor-letter", "opt-reviewer-comments"].forEach((id) => {
    $(id)?.addEventListener("input", persistSources);
  });

  restoreSources();
  renderNav();
  enableEditing(false);
  void loadLlmConfig();
  const initial = (location.hash || "#overview").replace("#", "");
  showView(VIEWS[initial] ? initial : "overview");

  const last = localStorage.getItem("aw-session");
  if (last) {
    api(`/api/sessions/${last}`)
      .then((session) => {
        applySession(session);
        if (!VIEWS[initial]) showView("overview");
      })
      .catch(() => localStorage.removeItem("aw-session"));
  }
})();
