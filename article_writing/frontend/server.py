"""Frontend API server for the article_writing plate.

Run from plate root:
  PYTHONPATH=. python3 -m uvicorn frontend.server:app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PLATE_ROOT = Path(__file__).resolve().parents[1]
if str(PLATE_ROOT) not in sys.path:
    sys.path.insert(0, str(PLATE_ROOT))

from article_writing.adapters import LiveLiteraturePort, MockLiteraturePort
from article_writing.adapters.ai_localbase_literature import DEFAULT_AI_LOCALBASE_URL
from article_writing.contracts import PaperBrief, PaperState, SectionDraft, SectionId
from article_writing.layout.manuscript import build_manuscript_markdown
from article_writing.llm import build_llm_client
from article_writing.llm.polish import polish_section_draft
from frontend.export_formats import export_word_and_pdf
from frontend.llm_config import load_llm_config, model_status, save_llm_config
from frontend.session_store import SessionStore, utc_now

FRONTEND_DIR = Path(__file__).resolve().parent
STATIC_DIR = FRONTEND_DIR / "static"
SESSIONS_DIR = PLATE_ROOT / "outputs" / "ui-sessions"
DEFAULT_LITERATURE_DB = (
    PLATE_ROOT.parent / "Article_repository" / "article_literature.sqlite3"
)

store = SessionStore(SESSIONS_DIR)

app = FastAPI(title="BioFLow Article Writing Frontend", version="0.2.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class StartRequest(BaseModel):
    use_llm: bool = False
    analysis_run_dir: str = ""
    analysis_bundle: str = ""
    brief_path: str = ""
    analysis_url: str = ""
    analysis_task_id: str = ""
    literature_query: str = ""
    literature_url: str = ""
    literature_db: str = ""
    use_live_literature: bool = False
    react_enabled: bool = True
    skills: list[str] = Field(default_factory=list)
    editor_letter: str = ""
    reviewer_comments: str = ""


class SaveSectionRequest(BaseModel):
    markdown: str


class ConfirmRequest(BaseModel):
    confirmed: bool = True


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    use_llm: bool = True


class LLMConfigRequest(BaseModel):
    enabled: bool | None = None
    provider: str | None = None
    model: str | None = None
    url: str | None = None
    timeout: float | None = None
    language: str | None = None


def _skills_connection() -> dict[str, Any]:
    root = PLATE_ROOT.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    folder = root / "bioflow_skills"
    try:
        from bioflow_skills import list_catalog

        writing = [entry.name for entry in list_catalog() if getattr(entry, "category", "") == "writing"]
        return {
            "ok": folder.is_dir() and bool(writing),
            "path": str(folder),
            "label": "bioflow_skills",
            "count": len(writing),
        }
    except Exception:  # noqa: BLE001
        return {"ok": False, "path": str(folder), "label": "bioflow_skills", "count": 0}


def _probe(url: str, timeout: float = 1.6) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return {"ok": 200 <= int(response.status) < 400, "status": int(response.status)}
    except urllib.error.HTTPError as error:
        return {"ok": 200 <= int(error.code) < 500, "status": int(error.code)}
    except Exception:  # noqa: BLE001
        return {"ok": False, "status": 0}


def _safe_http_url(raw: str) -> str:
    parsed = urlparse((raw or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="url must be http(s)")
    return parsed.geturl().rstrip("/")


def _fetch_json(url: str, timeout: float = 8.0) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"upstream failed: {error}") from error


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "frontend": str(FRONTEND_DIR), "sessions_dir": str(SESSIONS_DIR)}


def _literature_probe(literature_url: str) -> dict[str, Any]:
    """探测文献后端：当前后端是 ai-localbase（``/api/knowledge-bases``）。"""
    base = (literature_url or DEFAULT_AI_LOCALBASE_URL).rstrip("/")
    info: dict[str, Any] = {
        **_probe(f"{base}/api/knowledge-bases"),
        "url": base,
        "label": "文献知识库 (ai-localbase)",
    }
    if info.get("ok"):
        try:
            with urllib.request.urlopen(f"{base}/api/knowledge-bases", timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            items = payload.get("items") if isinstance(payload, dict) else payload
            info["knowledge_bases"] = len(items or [])
        except Exception:  # noqa: BLE001
            info["knowledge_bases"] = 0
    return info


def _sqlite_literature_rows(db_path: Path) -> int:
    """旧 SQLite 文献库行数（已停用，仅用于说明为什么不能走它）。"""
    if not db_path.is_file():
        return 0
    try:
        import sqlite3

        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            row = conn.execute("select count(*) from literature_metadata").fetchone()
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


@app.get("/api/connections")
def connections(
    analysis_url: str = "http://127.0.0.1:8000",
    literature_url: str = DEFAULT_AI_LOCALBASE_URL,
    knowledge_url: str = DEFAULT_AI_LOCALBASE_URL,
) -> dict[str, Any]:
    db_path = DEFAULT_LITERATURE_DB
    llm_config = load_llm_config()
    llm_state = model_status(llm_config)
    return {
        "writing": {"ok": True, "url": "http://127.0.0.1:8765/api/health", "label": "论文撰写"},
        "analysis": {
            **_probe(f"{analysis_url.rstrip('/')}/api/health"),
            "url": analysis_url.rstrip("/"),
            "label": "BioLLM 分析",
        },
        "literature": _literature_probe(literature_url),
        "knowledge": {
            **_probe(f"{knowledge_url.rstrip('/')}/health"),
            "url": knowledge_url.rstrip("/"),
            "label": "ai-localbase",
        },
        "ollama": {
            **_probe(f"{llm_config['url'].rstrip('/')}/api/tags"),
            "url": llm_config["url"],
            "label": "本地 Qwen",
        },
        "llm": {
            "ok": bool(llm_state["model_available"]),
            "provider": llm_config["provider"],
            "model": llm_config["model"],
            "url": llm_config["url"],
            "available_models": llm_state["available_models"],
            "warning": llm_state["warning"],
            "label": "论文 LLM",
        },
        "literature_sqlite": {
            "ok": _sqlite_literature_rows(db_path) > 0,
            "path": str(db_path),
            "rows": _sqlite_literature_rows(db_path),
            "deprecated": True,
            "label": "文献 SQLite（已停用，改用 ai-localbase）",
        },
        "fixture_package": {
            "ok": (PLATE_ROOT / "fixtures" / "biollm_metagenome" / "package").is_dir(),
            "path": str(PLATE_ROOT / "fixtures" / "biollm_metagenome" / "package"),
            "label": "宏基因组夹具",
        },
        "skills": _skills_connection(),
    }


@app.get("/api/llm-config")
def get_llm_config() -> dict[str, Any]:
    """当前 LLM 配置 + 模型可用性（前端设置面板用）。"""
    config = load_llm_config()
    state = model_status(config)
    return {
        "config": config,
        "available_models": state["available_models"],
        "model_available": state["model_available"],
        "warning": state["warning"],
    }


@app.put("/api/llm-config")
def update_llm_config(body: LLMConfigRequest) -> dict[str, Any]:
    patch = {key: value for key, value in body.model_dump().items() if value is not None}
    config = save_llm_config(patch)
    state = model_status(config)
    return {
        "config": config,
        "available_models": state["available_models"],
        "model_available": state["model_available"],
        "warning": state["warning"],
    }


@app.get("/api/upstream/analysis/tasks")
def analysis_tasks(base_url: str = "http://127.0.0.1:8000") -> dict[str, Any]:
    root = _safe_http_url(base_url)
    payload = _fetch_json(f"{root}/api/tasks")
    tasks = payload if isinstance(payload, list) else payload.get("tasks") or []
    return {"tasks": tasks, "base_url": root}


@app.get("/api/upstream/analysis/preview")
def analysis_preview(
    task_id: str = Query(min_length=1),
    base_url: str = "http://127.0.0.1:8000",
) -> dict[str, Any]:
    root = _safe_http_url(base_url)
    return {"preview": _fetch_json(f"{root}/api/tasks/{task_id}/preview"), "base_url": root}


@app.get("/api/literature/preview")
def literature_preview(
    query: str = Query(min_length=1),
    top_k: int = Query(default=5, ge=1, le=12),
    literature_url: str = "",
    literature_db: str = "",
    use_live: bool = False,
) -> dict[str, Any]:
    db = Path(literature_db).expanduser() if literature_db.strip() else DEFAULT_LITERATURE_DB
    live = bool(use_live or literature_url.strip())
    if live:
        port = LiveLiteraturePort(
            base_url=literature_url.strip(),
            db_path=db if db.is_file() else None,
        )
        source = "live"
    else:
        port = MockLiteraturePort(PLATE_ROOT / "fixtures")
        source = "fixture"
    hits = [hit.model_dump() for hit in port.search(query, top_k=top_k)]
    return {"query": query, "source": source, "hits": hits}


@app.get("/api/sessions")
def list_sessions() -> dict[str, Any]:
    return {"sessions": store.list_summaries()}


@app.post("/api/sessions")
def start_session(body: StartRequest) -> dict[str, Any]:
    llm_config = load_llm_config()
    if body.use_llm:
        state = model_status(llm_config)
        if state["model_available"] is False:
            raise HTTPException(status_code=400, detail=state["warning"] or "LLM 模型不可用")
    try:
        session = store.create_from_pipeline(
            use_llm=body.use_llm,
            fixtures_dir=None,
            analysis_run_dir=body.analysis_run_dir,
            analysis_bundle=body.analysis_bundle,
            brief_path=body.brief_path,
            analysis_url=body.analysis_url,
            analysis_task_id=body.analysis_task_id,
            literature_query=body.literature_query,
            literature_url=body.literature_url,
            literature_db=body.literature_db,
            use_live_literature=body.use_live_literature,
            react_enabled=body.react_enabled,
            selected_skills=body.skills,
            editor_letter=body.editor_letter,
            reviewer_comments=body.reviewer_comments,
            llm_provider=llm_config["provider"],
            llm_model=llm_config["model"],
            llm_url=llm_config["url"],
            llm_timeout=float(llm_config["timeout"]),
            llm_language=llm_config["language"],
        )
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"pipeline failed: {error}") from error
    return session.as_dict()


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session.as_dict()


@app.put("/api/sessions/{session_id}/sections/{section_id}")
def save_section(session_id: str, section_id: str, body: SaveSectionRequest) -> dict[str, Any]:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    section = session.sections.get(section_id)
    if section is None:
        raise HTTPException(status_code=404, detail="section not found")
    section.markdown = body.markdown
    section.confirmed = False
    section.updated_at = utc_now()
    store.save(session)
    return section.as_dict()


@app.post("/api/sessions/{session_id}/sections/{section_id}/confirm")
def confirm_section(session_id: str, section_id: str, body: ConfirmRequest) -> dict[str, Any]:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    section = session.sections.get(section_id)
    if section is None:
        raise HTTPException(status_code=404, detail="section not found")
    if body.confirmed and not section.markdown.strip():
        raise HTTPException(status_code=400, detail="empty section cannot be confirmed")
    section.confirmed = bool(body.confirmed)
    section.updated_at = utc_now()
    store.save(session)
    return {
        "section": section.as_dict(),
        "all_confirmed": all(s.confirmed for s in session.sections.values()),
        "confirmed_count": sum(1 for s in session.sections.values() if s.confirmed),
        "total_sections": len(session.sections),
    }


@app.post("/api/sessions/{session_id}/sections/{section_id}/chat")
def chat_section(session_id: str, section_id: str, body: ChatRequest) -> dict[str, Any]:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    section = session.sections.get(section_id)
    if section is None:
        raise HTTPException(status_code=404, detail="section not found")

    user_msg = body.message.strip()
    section.chat.append({"role": "user", "content": user_msg})

    if not body.use_llm:
        section.chat.append(
            {
                "role": "assistant",
                "content": "LLM 未启用。请直接在编辑器修改，或勾选「使用大模型」后重试。",
            }
        )
        store.save(session)
        return {"section": section.as_dict(), "applied": False}

    llm_config = load_llm_config()
    state = model_status(llm_config)
    if state["model_available"] is False:
        # 显式告警：模型不可用时立刻返回原因，不静默回退、不让用户白等
        warning = state["warning"] or f"模型 {llm_config['model']} 不可用"
        section.chat.append({"role": "assistant", "content": f"未调用大模型：{warning}"})
        section.updated_at = utc_now()
        store.save(session)
        return {
            "section": section.as_dict(),
            "applied": False,
            "error": warning,
            "warnings": [warning],
            "llm": {
                "provider": llm_config["provider"],
                "model": llm_config["model"],
                "polished": False,
            },
        }

    client = build_llm_client(
        enabled=True,
        provider=llm_config["provider"],
        model=llm_config["model"],
        url=llm_config["url"],
        timeout=float(llm_config["timeout"]),
    )
    try:
        sid = SectionId(section_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    draft = SectionDraft(section=sid, title=section.title, markdown=section.markdown)
    instructions = (
        f"Author revision request for this section only:\n{user_msg}\n\n"
        "Apply the request while obeying the hard factual rules. "
        "Keep bilingual English + 中文 structure if already present; "
        "otherwise keep the draft language and improve journal style."
    )
    paper_context = (
        f"Title: {session.title}\n"
        f"Question: {session.research_question}\n"
        "Do not invent numbers/findings beyond the current section draft."
    )
    polished = polish_section_draft(
        draft,
        client,
        language="en+zh",
        instructions=instructions,
        paper_context=paper_context,
        sections={sid},
    )
    applied = bool((polished.metadata.get("llm") or {}).get("polished"))
    error = ""
    if applied:
        section.markdown = polished.markdown
        section.confirmed = False
        note = "已按你的要求改写本章，并写回编辑器。请审阅后再点「确认本章」。"
        if polished.warnings:
            note += "\n\n注意（本次改写有告警）：\n" + "\n".join(f"- {w}" for w in polished.warnings)
    else:
        warn = "; ".join(polished.warnings) if polished.warnings else "polish rejected or failed"
        error = warn
        note = (
            f"未能应用改写（保留原文）：{warn}\n"
            f"当前模型：{llm_config['provider']} / {llm_config['model']} @ {llm_config['url']}。"
            "可在「模型设置」中更换为已安装的模型后重试。"
        )
    section.chat.append({"role": "assistant", "content": note})
    section.updated_at = utc_now()
    store.save(session)
    return {
        "section": section.as_dict(),
        "applied": applied,
        "error": error,
        "llm": polished.metadata.get("llm"),
        "warnings": polished.warnings,
    }


@app.post("/api/sessions/{session_id}/export")
def export_manuscript(session_id: str) -> dict[str, Any]:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    missing = [s.section for s in session.sections.values() if not s.confirmed]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"请先确认全部章节后再统一排版：未确认 {', '.join(missing)}",
        )

    order = [SectionId(s) for s in session.section_order]
    state = PaperState(
        run_id=session.run_id,
        brief=PaperBrief(
            title=session.title,
            research_question=session.research_question,
        ),
        section_order=order,
    )
    for sid in order:
        sec = session.sections[sid.value]
        state.add_draft(SectionDraft(section=sid, title=sec.title, markdown=sec.markdown))

    manuscript = build_manuscript_markdown(state)
    out_dir = Path(session.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_sections = out_dir / "final_sections"
    final_sections.mkdir(parents=True, exist_ok=True)
    for sid, sec in session.sections.items():
        (final_sections / f"{sid}.md").write_text(sec.markdown, encoding="utf-8")

    formats = export_word_and_pdf(
        manuscript,
        out_dir,
        basename="manuscript_final",
        title=session.title or "Manuscript",
        search_roots=[
            out_dir,
            out_dir / "pipeline",
            out_dir / "pipeline" / "figures",
            PLATE_ROOT,
            PLATE_ROOT / "fixtures",
        ],
    )

    session.manuscript = manuscript
    session.exported_path = formats["markdown"]
    session.docx_path = formats.get("docx") or None
    session.pdf_path = formats.get("pdf") or None
    session.pdf_error = formats.get("pdf_error") or None
    store.save(session)

    payload = session.as_dict()
    return {
        "exported_path": formats["markdown"],
        "docx_path": formats.get("docx") or "",
        "pdf_path": formats.get("pdf") or "",
        "docx_error": formats.get("docx_error") or "",
        "pdf_error": formats.get("pdf_error") or "",
        "download": {
            "markdown": f"/api/sessions/{session_id}/download/markdown",
            "docx": f"/api/sessions/{session_id}/download/docx",
            "pdf": f"/api/sessions/{session_id}/download/pdf",
        },
        "manuscript": manuscript,
        "session": payload,
    }


@app.get("/api/sessions/{session_id}/download/{fmt}")
def download_export(session_id: str, fmt: str) -> FileResponse:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    out_dir = Path(session.output_dir)
    mapping = {
        "markdown": (out_dir / "manuscript_final.md", "text/markdown", "manuscript_final.md"),
        "md": (out_dir / "manuscript_final.md", "text/markdown", "manuscript_final.md"),
        "docx": (
            out_dir / "manuscript_final.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "manuscript_final.docx",
        ),
        "pdf": (out_dir / "manuscript_final.pdf", "application/pdf", "manuscript_final.pdf"),
        "word": (
            out_dir / "manuscript_final.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "manuscript_final.docx",
        ),
    }
    key = fmt.lower()
    if key not in mapping:
        raise HTTPException(status_code=400, detail="fmt must be markdown|docx|pdf")
    path, media, filename = mapping[key]
    if not path.is_file():
        if session.manuscript and key in {"docx", "word", "pdf"}:
            formats = export_word_and_pdf(
                session.manuscript,
                out_dir,
                basename="manuscript_final",
                title=session.title or "Manuscript",
                search_roots=[out_dir, out_dir / "pipeline", PLATE_ROOT],
            )
            if key == "pdf" and not formats.get("pdf"):
                raise HTTPException(
                    status_code=500,
                    detail=formats.get("pdf_error") or "PDF 生成失败",
                )
            path = Path(formats["docx"] if key in {"docx", "word"} else formats["pdf"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{filename} 尚未生成，请先统一排版导出")
    return FileResponse(path, media_type=media, filename=filename)
