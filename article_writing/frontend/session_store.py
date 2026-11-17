"""Session store for the writing plate frontend (lives under frontend/)."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from article_writing.contracts import DEFAULT_SECTION_ORDER, SectionId
from article_writing.export.bundle import export_bundle
from article_writing.orchestrator import WritingPipeline

PLATE_ROOT = Path(__file__).resolve().parents[1]

_PROGRESS_NAMES = {
    "methods": "方法",
    "results": "结果",
    "discussion": "讨论",
    "introduction": "引言",
    "abstract": "摘要",
    "conclusion": "结论",
    "back_matter": "文后",
    "translation": "中文译文",
}


def _progress_text(section: str, index: int, total: int) -> str:
    name = _PROGRESS_NAMES.get(section, section)
    if section == "translation" or total <= 0:
        return f"正在准备{name}"
    return f"正在写{name}（{index}/{total}）"


def _title_from_topic(topic: str) -> str:
    text = " ".join(topic.split())
    if not text:
        return "正在生成稿件"
    for mark in ("。", ".", "！", "!", "？", "?"):
        if mark in text:
            text = text.split(mark, 1)[0]
            break
    return text.strip()[:80] or "正在生成稿件"


SECTION_LABELS = {
    SectionId.abstract: ("Abstract", "摘要"),
    SectionId.introduction: ("Introduction", "引言"),
    SectionId.methods: ("Methods", "方法"),
    SectionId.results: ("Results", "结果"),
    SectionId.discussion: ("Discussion", "讨论"),
    SectionId.conclusion: ("Conclusions", "结论"),
    SectionId.back_matter: ("Back Matter", "文后"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SectionState:
    section: str
    title: str
    title_zh: str
    markdown: str
    confirmed: bool = False
    chat: list[dict[str, str]] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "title": self.title,
            "title_zh": self.title_zh,
            "markdown": self.markdown,
            "confirmed": self.confirmed,
            "chat": list(self.chat),
            "updated_at": self.updated_at,
        }


@dataclass
class WritingSession:
    session_id: str
    run_id: str
    title: str
    research_question: str
    sections: dict[str, SectionState]
    section_order: list[str]
    output_dir: str
    created_at: str = field(default_factory=utc_now)
    exported_path: str | None = None
    manuscript: str | None = None
    docx_path: str | None = None
    pdf_path: str | None = None
    pdf_error: str | None = None
    source: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    status: str = "ready"
    progress: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "title": self.title,
            "research_question": self.research_question,
            "status": self.status,
            "progress": self.progress,
            "error": self.error,
            "section_order": list(self.section_order),
            "sections": {k: v.as_dict() for k, v in self.sections.items()},
            "all_confirmed": self.status == "ready" and bool(self.sections) and all(s.confirmed for s in self.sections.values()),
            "confirmed_count": sum(1 for s in self.sections.values() if s.confirmed),
            "total_sections": len(self.sections),
            "output_dir": self.output_dir,
            "exported_path": self.exported_path,
            "docx_path": self.docx_path,
            "pdf_path": self.pdf_path,
            "pdf_error": self.pdf_error,
            "manuscript": self.manuscript,
            "created_at": self.created_at,
            "source": dict(self.source or {}),
            "warnings": list(self.warnings or []),
        }

    def summary(self) -> dict[str, Any]:
        payload = self.as_dict()
        payload.pop("manuscript", None)
        payload["sections"] = {
            key: {
                "section": val["section"],
                "title": val["title"],
                "title_zh": val["title_zh"],
                "confirmed": val["confirmed"],
                "updated_at": val["updated_at"],
                "chars": len(val.get("markdown") or ""),
            }
            for key, val in payload["sections"].items()
        }
        return payload


class SessionStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, WritingSession] = {}
        self._lock = threading.Lock()

    def path_for(self, session_id: str) -> Path:
        return self.root / session_id

    def save(self, session: WritingSession) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
            folder = self.path_for(session.session_id)
            folder.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(session.as_dict(), indent=2, ensure_ascii=False)
            target = folder / "session.json"
            temporary = folder / "session.json.tmp"
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(target)
            sec_dir = folder / "sections"
            sec_dir.mkdir(parents=True, exist_ok=True)
            for sid, section in session.sections.items():
                (sec_dir / f"{sid}.md").write_text(section.markdown, encoding="utf-8")

    def get(self, session_id: str) -> WritingSession | None:
        with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]
            path = self.path_for(session_id) / "session.json"
            if not path.is_file():
                return None
            return self._load(session_id, path)

    def _load(self, session_id: str, path: Path) -> WritingSession:
        data = json.loads(path.read_text(encoding="utf-8"))
        sections = {
            key: SectionState(
                section=val["section"],
                title=val["title"],
                title_zh=val["title_zh"],
                markdown=val["markdown"],
                confirmed=bool(val.get("confirmed")),
                chat=list(val.get("chat") or []),
                updated_at=val.get("updated_at") or utc_now(),
            )
            for key, val in (data.get("sections") or {}).items()
        }
        session = WritingSession(
            session_id=data["session_id"],
            run_id=data.get("run_id") or data["session_id"],
            title=data.get("title") or "",
            research_question=data.get("research_question") or "",
            sections=sections,
            section_order=list(data.get("section_order") or list(sections)),
            output_dir=data.get("output_dir") or str(self.path_for(session_id)),
            created_at=data.get("created_at") or utc_now(),
            exported_path=data.get("exported_path"),
            manuscript=data.get("manuscript"),
            docx_path=data.get("docx_path"),
            pdf_path=data.get("pdf_path"),
            pdf_error=data.get("pdf_error"),
            source=dict(data.get("source") or {}),
            warnings=list(data.get("warnings") or []),
            status=data.get("status") or "ready",
            progress=data.get("progress") or "",
            error=data.get("error") or "",
        )
        self._sessions[session_id] = session
        return session

    def list_summaries(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return items
        folders = sorted(
            (path for path in self.root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for folder in folders:
            session = self.get(folder.name)
            if session is not None:
                items.append(session.summary())
        return items

    def create_from_pipeline(
        self,
        *,
        use_llm: bool = False,
        fixtures_dir: Path | None = None,
        analysis_run_dir: str = "",
        analysis_bundle: str = "",
        brief_path: str = "",
        brief_text: str = "",
        analysis_url: str = "",
        analysis_task_id: str = "",
        literature_query: str = "",
        literature_url: str = "",
        literature_db: str = "",
        knowledge_base_id: str = "",
        literature_headers: dict[str, str] | None = None,
        use_live_literature: bool = False,
        react_enabled: bool = True,
        selected_skills: list[str] | None = None,
        editor_letter: str = "",
        reviewer_comments: str = "",
        llm_provider: str = "ollama",
        llm_model: str = "qwen3:14b",
        llm_url: str = "http://127.0.0.1:11434",
        llm_timeout: float = 300.0,
        llm_language: str = "en+zh",
        session_id: str = "",
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> WritingSession:
        session_id = session_id.strip() or uuid.uuid4().hex[:12]
        run_id = f"ui-{session_id}"
        out = self.path_for(session_id) / "pipeline"
        fixtures = fixtures_dir or (PLATE_ROOT / "fixtures")
        has_live_analysis = bool(
            analysis_run_dir.strip()
            or analysis_bundle.strip()
            or (analysis_url.strip() and analysis_task_id.strip())
        )
        topic = " ".join(brief_text.split())
        has_live_brief = bool(brief_path.strip() or topic)
        query = (literature_query or "").strip() or "autism gut microbiota"
        live_lit = bool(use_live_literature or literature_url.strip() or literature_db.strip())
        adapter_mode = (
            "live" if (has_live_analysis or has_live_brief or live_lit) else "mock"
        )
        pipeline = WritingPipeline(
            fixtures_dir=fixtures,
            literature_query=query,
            adapter_mode=adapter_mode,
            literature_url=literature_url.strip(),
            literature_db=literature_db.strip() or None,
            knowledge_base_id=knowledge_base_id.strip(),
            literature_headers=literature_headers,
            literature_enabled=live_lit,
            analysis_run_dir=analysis_run_dir.strip(),
            analysis_bundle_path=analysis_bundle.strip(),
            analysis_url=analysis_url.strip(),
            analysis_task_id=analysis_task_id.strip(),
            brief_path=brief_path.strip(),
            brief_text=topic,
            react_enabled=react_enabled,
            llm_enabled=use_llm,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_url=llm_url,
            llm_timeout=llm_timeout,
            llm_language=llm_language,
            selected_skills=selected_skills,
            editor_letter=editor_letter,
            reviewer_comments=reviewer_comments,
            on_progress=on_progress,
        )
        state = pipeline.run(run_id=run_id)
        roots = [PLATE_ROOT, fixtures]
        package_root = getattr(pipeline.analysis_port, "package_root", None)
        if package_root:
            roots.insert(0, Path(package_root))
        export_bundle(
            state,
            out,
            consistency_report=pipeline.last_consistency_report,
            figure_search_roots=roots,
            skill_outputs=pipeline.skill_outputs,
        )

        sections: dict[str, SectionState] = {}
        order: list[str] = []
        for section_id in state.section_order or DEFAULT_SECTION_ORDER:
            draft = state.drafts.get(section_id.value)
            if draft is None:
                continue
            en, zh = SECTION_LABELS.get(section_id, (draft.title, draft.title))
            sections[section_id.value] = SectionState(
                section=section_id.value,
                title=en,
                title_zh=zh,
                markdown=draft.markdown,
            )
            order.append(section_id.value)

        warnings: list[str] = []
        bundle_path = out / "writing_bundle.json"
        if bundle_path.is_file():
            try:
                bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
                warnings = list(bundle.get("warnings") or [])
            except json.JSONDecodeError:
                warnings = []
        session = WritingSession(
            session_id=session_id,
            run_id=run_id,
            title=state.brief.title,
            research_question=state.brief.research_question,
            sections=sections,
            section_order=order,
            output_dir=str(self.path_for(session_id)),
            source={
                "adapter_mode": adapter_mode,
                "use_llm": use_llm,
                "react_enabled": react_enabled,
                "literature_query": query,
                "literature_url": literature_url.strip(),
                "literature_db": literature_db.strip(),
                "knowledge_base_id": knowledge_base_id.strip(),
                "use_live_literature": live_lit,
                "analysis_run_dir": analysis_run_dir.strip(),
                "analysis_bundle": analysis_bundle.strip(),
                "analysis_url": analysis_url.strip(),
                "analysis_task_id": analysis_task_id.strip(),
                "references": [c.model_dump() for c in state.citations],
                "brief_path": brief_path.strip(),
                "brief_text": topic,
                "skills": sorted(pipeline.selected_skills),
                "editor_letter": editor_letter.strip(),
                "reviewer_comments": reviewer_comments.strip(),
            },
            warnings=warnings,
        )
        self.save(session)
        return session

    def begin_generation(self, **kwargs: Any) -> WritingSession:
        session_id = uuid.uuid4().hex[:12]
        topic = " ".join(str(kwargs.get("brief_text") or "").split())
        session = WritingSession(
            session_id=session_id,
            run_id=f"ui-{session_id}",
            title=_title_from_topic(topic),
            research_question=topic,
            sections={},
            section_order=[],
            output_dir=str(self.path_for(session_id)),
            status="running",
            progress="正在准备稿件",
            source={
                "brief_text": topic,
                "analysis_task_id": str(kwargs.get("analysis_task_id") or "").strip(),
                "analysis_run_dir": str(kwargs.get("analysis_run_dir") or "").strip(),
                "use_live_literature": bool(kwargs.get("use_live_literature")),
                "knowledge_base_id": str(kwargs.get("knowledge_base_id") or "").strip(),
            },
        )
        self.save(session)
        threading.Thread(
            target=self._finish_generation,
            args=(session_id, dict(kwargs)),
            daemon=True,
            name=f"writing-{session_id}",
        ).start()
        return session

    def _finish_generation(self, session_id: str, kwargs: dict[str, Any]) -> None:
        def on_progress(section: str, index: int, total: int) -> None:
            try:
                current = self.get(session_id)
                if current is None or current.status == "failed":
                    return
                current.status = "running"
                current.progress = _progress_text(section, index, total)
                self.save(current)
            except Exception:
                return

        try:
            finished = self.create_from_pipeline(
                session_id=session_id,
                on_progress=on_progress,
                **kwargs,
            )
        except Exception as error:
            current = self.get(session_id)
            if current is None:
                return
            current.status = "failed"
            current.error = str(error)
            current.progress = "生成没有完成"
            self.save(current)
            return
        if isinstance(finished, WritingSession):
            finished.status = "ready"
            finished.progress = "稿件已生成"
            finished.error = ""
            self.save(finished)
