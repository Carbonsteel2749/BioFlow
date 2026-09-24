"""Run manuscript sections in IMRaD-style order and maintain PaperState."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional

from article_writing.adapters import AdapterBundle, build_adapters
from article_writing.consistency import ConsistencyMode, harden_paper_state
from article_writing.contracts import DEFAULT_SECTION_ORDER, PaperState, SectionId, SectionInput
from article_writing.evidence import EvidenceMode, apply_evidence_binding
from article_writing.export.bundle import export_bundle
from article_writing.llm import LLMClient, LLMDisabled, build_llm_client, polish_section_draft
from article_writing.react import run_related_work_react
from article_writing.registry import create_section
from article_writing.skills.client import SkillClient
from article_writing.skills.hooks import (
    POLISH_SKILL,
    RESPONSE_SKILL,
    REVIEW_SKILL,
    WRITING_SKILL,
    polish_instructions,
    response_markdown,
    review_markdown,
    writing_instructions,
)
from article_writing.layout.manuscript import build_manuscript_markdown


DEFAULT_ORDER: List[SectionId] = list(DEFAULT_SECTION_ORDER)


def _normalize_section_order(
    section_order: Optional[Iterable[SectionId | str]],
) -> List[SectionId]:
    """Return one complete, duplicate-free permutation of the manuscript sections."""

    configured_order = DEFAULT_ORDER if section_order is None else section_order
    try:
        order = [item if isinstance(item, SectionId) else SectionId(item) for item in configured_order]
    except ValueError as error:
        raise ValueError(f"section_order contains an unknown section: {error}") from error

    missing = [section.value for section in DEFAULT_ORDER if section not in order]
    duplicates = [section.value for section in DEFAULT_ORDER if order.count(section) > 1]
    if missing or duplicates or len(order) != len(DEFAULT_ORDER):
        details: list[str] = []
        if missing:
            details.append(f"missing={missing}")
        if duplicates:
            details.append(f"duplicates={duplicates}")
        raise ValueError(
            "section_order must contain each manuscript section exactly once"
            + (f" ({', '.join(details)})" if details else "")
        )
    return order


class WritingPipeline:
    """Orchestrates sections. Depends only on Port ABCs via AdapterBundle."""

    def __init__(
        self,
        fixtures_dir: Path | str | None = None,
        literature_query: str = "autism gut microbiota differential expression",
        section_order: Optional[Iterable[SectionId | str]] = None,
        *,
        adapter_mode: str = "mock",
        adapters: AdapterBundle | None = None,
        literature_url: str = "",
        literature_timeout: float = 30.0,
        literature_db: Path | str | None = None,
        analysis_bundle_path: str = "",
        analysis_run_dir: str = "",
        analysis_url: str = "",
        analysis_task_id: str = "",
        brief_path: str = "",
        evidence_mode: EvidenceMode | str = EvidenceMode.warn,
        react_enabled: bool = True,
        react_max_steps: int = 8,
        react_search_top_k: int = 3,
        consistency_mode: ConsistencyMode | str = ConsistencyMode.warn,
        llm: LLMClient | None = None,
        llm_enabled: bool = False,
        llm_provider: str = "ollama",
        llm_model: str = "qwen:7b",
        llm_url: str = "http://127.0.0.1:11434",
        llm_api_key: str | None = None,
        llm_timeout: float = 180.0,
        llm_language: str = "en+zh",
        llm_instructions: str = "",
        skills_enabled: bool = False,
        selected_skills: list[str] | None = None,
        editor_letter: str = "",
        reviewer_comments: str = "",
    ) -> None:
        self.fixtures_dir = fixtures_dir
        self.literature_query = literature_query
        self.section_order = _normalize_section_order(section_order)
        self.evidence_mode = (
            evidence_mode
            if isinstance(evidence_mode, EvidenceMode)
            else EvidenceMode(evidence_mode)
        )
        self.react_enabled = bool(react_enabled)
        self.react_max_steps = max(1, int(react_max_steps))
        self.react_search_top_k = max(1, int(react_search_top_k))
        self.consistency_mode = (
            consistency_mode
            if isinstance(consistency_mode, ConsistencyMode)
            else ConsistencyMode(consistency_mode)
        )
        self.llm_language = llm_language or "en+zh"
        self.llm_instructions = llm_instructions or ""
        self.llm = llm or build_llm_client(
            enabled=llm_enabled,
            provider=llm_provider,
            model=llm_model,
            url=llm_url,
            api_key=llm_api_key,
            timeout=llm_timeout,
        )
        catalog = {WRITING_SKILL, POLISH_SKILL, REVIEW_SKILL, RESPONSE_SKILL}
        if selected_skills is not None:
            chosen = {name for name in selected_skills if name in catalog}
        elif skills_enabled:
            chosen = set(catalog)
        else:
            chosen = set()
        self.selected_skills = chosen
        self.skills_enabled = bool(chosen)
        self.editor_letter = editor_letter or ""
        self.reviewer_comments = reviewer_comments or ""
        self.skill_client = SkillClient(enabled=self.skills_enabled)
        self.skill_outputs: dict[str, str] = {}
        self.last_consistency_report = None
        self.adapters = adapters or build_adapters(
            mode=adapter_mode,
            fixtures_dir=fixtures_dir,
            literature_url=literature_url,
            literature_timeout=literature_timeout,
            literature_db=literature_db,
            analysis_bundle_path=analysis_bundle_path,
            analysis_run_dir=analysis_run_dir,
            analysis_url=analysis_url,
            analysis_task_id=analysis_task_id,
            brief_path=brief_path,
        )
        self.brief_port = self.adapters.brief
        self.analysis_port = self.adapters.analysis
        self.literature_port = self.adapters.literature

    def run(self, run_id: str = "writing-demo-001") -> PaperState:
        brief = self.brief_port.load()
        analysis = self.analysis_port.load()
        literature = self.literature_port.search(self.literature_query, top_k=5)
        react_payload: dict = {"enabled": False, "trajectory": None}

        if self.react_enabled:
            react_result = run_related_work_react(
                brief=brief,
                literature_port=self.literature_port,
                seed_query=self.literature_query,
                max_steps=self.react_max_steps,
                search_top_k=self.react_search_top_k,
            )
            literature = react_result.hits or literature
            react_payload = {
                "enabled": True,
                "trajectory": react_result.trajectory.model_dump(),
            }

        state = PaperState(run_id=run_id, brief=brief, section_order=self.section_order)
        if analysis.limitations:
            seen: set[str] = set()
            for item in analysis.limitations:
                cleaned = " ".join(item.split())
                key = cleaned.casefold()
                if cleaned and key not in seen:
                    state.limitations.append(cleaned)
                    seen.add(key)

        paper_context = _build_paper_context(brief, analysis)

        for section_id in self.section_order:
            section = create_section(section_id)
            payload: dict = {}
            # Literature-informed sections receive ReAct trajectory metadata when enabled.
            if section_id in {SectionId.introduction, SectionId.discussion, SectionId.back_matter}:
                payload["react"] = react_payload

            section_input = SectionInput(
                run_id=run_id,
                section=section_id,
                brief=brief,
                analysis=analysis,
                literature=literature,
                payload=payload,
            )
            draft = section.run(section_input, state=state)
            if draft.section != section_id:
                raise ValueError(
                    f"section {section_id.value!r} returned a draft for "
                    f"{draft.section.value!r}"
                )
            # Template draft first; optional LLM polish must not invent facts.
            extra = self.llm_instructions
            sec_extra = (draft.metadata or {}).get("llm_instructions_extra") or ""
            if sec_extra:
                extra = f"{extra}\n\n{sec_extra}".strip() if extra else str(sec_extra)
            skill_meta: dict = {
                "enabled": self.skills_enabled,
                "selected": sorted(self.selected_skills),
            }
            write_rules = polish_rules = ""
            write_handoff = polish_handoff = None
            if WRITING_SKILL in self.selected_skills:
                write_rules, write_handoff = writing_instructions(
                    self.skill_client,
                    section=section_id,
                    brief=brief,
                    analysis=analysis,
                )
            if POLISH_SKILL in self.selected_skills:
                polish_rules, polish_handoff = polish_instructions(
                    self.skill_client,
                    section=section_id,
                    draft_text=draft.markdown,
                )
            if write_rules:
                extra = f"{extra}\n\n{write_rules}".strip() if extra else write_rules
            if polish_rules:
                extra = f"{extra}\n\n{polish_rules}".strip() if extra else polish_rules
            skill_meta["writing"] = bool(write_handoff)
            skill_meta["polishing"] = bool(polish_handoff)
            if write_handoff or polish_handoff:
                skill_meta["playbook_chars"] = len(extra)
            if getattr(self.llm, "enabled", False) and not isinstance(self.llm, LLMDisabled):
                draft = polish_section_draft(
                    draft,
                    self.llm,
                    language=self.llm_language,
                    instructions=extra,
                    paper_context=paper_context,
                )
            elif extra and self.skills_enabled:
                draft.warnings.append(
                    f"skills playbook attached for {section_id.value}; "
                    "enable --llm to execute drafting/polish with the local model"
                )
            meta = dict(draft.metadata or {})
            meta["skills"] = skill_meta
            draft = draft.model_copy(update={"metadata": meta})
            draft = apply_evidence_binding(
                draft,
                section_input=section_input,
                state=state,
                mode=self.evidence_mode,
            )
            state.add_draft(draft)

        state, report = harden_paper_state(state, mode=self.consistency_mode)
        self.last_consistency_report = report
        if self.skills_enabled and (
            REVIEW_SKILL in self.selected_skills or RESPONSE_SKILL in self.selected_skills
        ):
            manuscript = build_manuscript_markdown(state)
            if REVIEW_SKILL in self.selected_skills:
                review = review_markdown(
                    self.skill_client,
                    state=state,
                    manuscript=manuscript,
                    analysis=analysis,
                    llm=self.llm,
                )
                if review:
                    self.skill_outputs["review.md"] = review
            if RESPONSE_SKILL in self.selected_skills:
                response = response_markdown(
                    self.skill_client,
                    state=state,
                    manuscript=manuscript,
                    editor_letter=self.editor_letter,
                    reviewer_comments=self.reviewer_comments,
                    llm=self.llm,
                )
                if response:
                    self.skill_outputs["response.md"] = response
        if self.skills_enabled:
            for note in self.skill_client.warnings:
                self.skill_outputs.setdefault("_warnings", "")
                self.skill_outputs["_warnings"] += note + "\n"
        return state

    def run_and_export(self, run_id: str, output_dir: Path | str) -> Path:
        state = self.run(run_id=run_id)
        roots: list[Path] = [Path.cwd()]
        package_root = getattr(self.analysis_port, "package_root", None)
        if package_root:
            roots.insert(0, Path(package_root))
        if self.fixtures_dir:
            roots.insert(0, Path(self.fixtures_dir))
            roots.insert(0, Path(self.fixtures_dir).parent)
        return export_bundle(
            state,
            output_dir,
            consistency_report=self.last_consistency_report,
            figure_search_roots=roots,
            skill_outputs=self.skill_outputs,
        )


def _build_paper_context(brief, analysis) -> str:
    """Compact immutable context so polish can link Methods↔Results↔Conclusions."""

    lines = [
        f"Title: {brief.title}",
        f"Question: {brief.research_question}",
        f"System: {brief.organism_or_system}",
        f"Modality: {brief.data_modality}",
    ]
    if analysis is not None:
        if analysis.summary:
            lines.append(f"Analysis summary: {analysis.summary}")
        if analysis.key_findings:
            lines.append("Key findings (immutable):")
            for item in analysis.key_findings:
                lines.append(f"- {item}")
        if analysis.metrics:
            lines.append("Metrics (immutable): " + ", ".join(
                f"{k}={v}" for k, v in analysis.metrics.items()
            ))
        if analysis.figure_refs:
            lines.append(
                "Figures (immutable ids/paths): "
                + "; ".join(f"{f.figure_id}:{f.path}" for f in analysis.figure_refs)
            )
        if analysis.limitations:
            lines.append("Limitations (immutable): " + " | ".join(analysis.limitations))
    lines.append(
        "Narrative duty: coherently connect experimental methods with results and "
        "conclusions; never change upstream numbers or conclusions."
    )
    return "\n".join(lines)
