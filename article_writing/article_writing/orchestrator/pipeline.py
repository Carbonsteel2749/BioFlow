"""Run manuscript sections in IMRaD-style order and maintain PaperState."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, List, Optional

from article_writing.adapters import AdapterBundle, build_adapters
from article_writing.adapters.materials import material_instructions
from article_writing.adapters.base import EmptyLiteraturePort
from article_writing.adapters.brief_from_analysis import brief_from_analysis, brief_from_topic
from article_writing.consistency import ConsistencyMode, harden_paper_state
from article_writing.contracts import DEFAULT_SECTION_ORDER, PaperState, SectionId, SectionInput
from article_writing.evidence import EvidenceMode, apply_evidence_binding
from article_writing.export.bundle import export_bundle
from article_writing.adapters.archive_catalog import catalog_from_raw
from article_writing.llm import LLMClient, LLMDisabled, build_llm_client, polish_section_draft
from article_writing.llm.compose_from_catalog import (
    WRITE_ORDER,
    compose_section_from_catalog,
    facts_for_section,
    prior_prose_for,
    translate_section_text,
)
from article_writing.react import run_related_work_react
from article_writing.registry import create_section
from article_writing import section_library
from article_writing.skills import bio_skills, library_sections, section_runner
from article_writing.skills import figures as figure_source
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
    """Return the user's section choice: any non-empty, duplicate-free list from the section library."""

    configured_order = DEFAULT_ORDER if section_order is None else list(section_order)
    try:
        order = [item if isinstance(item, SectionId) else SectionId(item) for item in configured_order]
    except ValueError as error:
        raise ValueError(f"section_order contains an unknown section: {error}") from error
    if not order:
        raise ValueError("section_order must contain at least one section")
    duplicates = sorted({section.value for section in order if order.count(section) > 1})
    if duplicates:
        raise ValueError(f"section_order must not repeat a section (duplicates={duplicates})")
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
        knowledge_base_id: str = "",
        literature_headers: dict[str, str] | None = None,
        literature_enabled: bool = True,
        analysis_bundle_path: str = "",
        analysis_run_dir: str = "",
        analysis_url: str = "",
        analysis_task_id: str = "",
        brief_path: str = "",
        brief_text: str = "",
        derive_brief_from_analysis: bool | None = None,
        evidence_mode: EvidenceMode | str = EvidenceMode.warn,
        react_enabled: bool = True,
        react_max_steps: int = 8,
        react_search_top_k: int = 3,
        consistency_mode: ConsistencyMode | str = ConsistencyMode.warn,
        llm: LLMClient | None = None,
        llm_enabled: bool = False,
        llm_provider: str = "ollama",
        llm_model: str = "qwen3:14b",
        llm_url: str = "http://127.0.0.1:11434",
        llm_api_key: str | None = None,
        llm_timeout: float = 180.0,
        llm_language: str = "en+zh",
        llm_instructions: str = "",
        skills_enabled: bool = False,
        selected_skills: list[str] | None = None,
        editor_letter: str = "",
        reviewer_comments: str = "",
        literature_document_ids: list[str] | None = None,
        on_progress: Callable[[str, int, int], None] | None = None,
        literature_refs: list[dict] | None = None,
        figure_ids: list[str] | None = None,
    ) -> None:
        # Chart-module figures chosen for this manuscript (project_figures ids).
        self.figure_ids = [str(i) for i in (figure_ids or []) if str(i).strip()]
        self.figures: list[dict] = []
        self.quality_report: dict = {}
        # Papers the author chose for this manuscript: [{doi, title, authors, year}, ...]
        self.literature_refs = [dict(ref) for ref in (literature_refs or []) if isinstance(ref, dict)]
        self.reference_cards: list[dict] = []
        self.fixtures_dir = fixtures_dir
        self.literature_query = literature_query
        has_live_analysis = bool(
            str(analysis_bundle_path or "").strip()
            or str(analysis_run_dir or "").strip()
            or (str(analysis_url or "").strip() and str(analysis_task_id or "").strip())
        )
        self.brief_text = " ".join(str(brief_text or "").split())
        self.on_progress = on_progress
        if derive_brief_from_analysis is None:
            self.derive_brief_from_analysis = (
                has_live_analysis
                and not str(brief_path or "").strip()
                and not self.brief_text
            )
        else:
            self.derive_brief_from_analysis = bool(derive_brief_from_analysis)
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
        self.literature_document_ids = [item.strip() for item in (literature_document_ids or []) if str(item).strip()]
        self.skill_client = SkillClient(enabled=self.skills_enabled)
        self.skill_outputs: dict[str, str] = {}
        self.literature_warning = ""
        self.last_consistency_report = None
        self.adapters = adapters or build_adapters(
            mode=adapter_mode,
            fixtures_dir=fixtures_dir,
            literature_url=literature_url,
            literature_timeout=literature_timeout,
            literature_db=literature_db,
            knowledge_base_id=knowledge_base_id,
            literature_headers=literature_headers,
            analysis_bundle_path=analysis_bundle_path,
            analysis_run_dir=analysis_run_dir,
            analysis_url=analysis_url,
            analysis_task_id=analysis_task_id,
            brief_path=brief_path,
        )
        self.brief_port = self.adapters.brief
        self.analysis_port = self.adapters.analysis
        self.literature_port = self.adapters.literature if literature_enabled else EmptyLiteraturePort()

    def run(self, run_id: str = "writing-demo-001") -> PaperState:
        brief = self.brief_port.load()
        print("loading analysis package…", flush=True)
        analysis = self.analysis_port.load()
        if self.brief_text:
            brief = brief_from_topic(self.brief_text)
            print(f"brief from typed topic: {brief.title}", flush=True)
        elif self.derive_brief_from_analysis:
            brief = brief_from_analysis(analysis)
            print(f"brief derived from package: {brief.title}", flush=True)
        material_report = analysis.raw.get("materials") if isinstance(analysis.raw, dict) else None
        catalog = []
        if isinstance(getattr(analysis, "raw", None), dict):
            catalog = analysis.raw.get("archive_catalog") or []
        print(f"archive catalog items: {len(catalog)}", flush=True)
        print("retrieving literature (structure examples only)…", flush=True)
        try:
            if self.literature_document_ids and hasattr(self.literature_port, "hits_for_documents"):
                literature = self.literature_port.hits_for_documents(self.literature_document_ids)
            else:
                literature = self.literature_port.search(self.literature_query, top_k=5)
        except Exception as error:  # noqa: BLE001 — missing literature falls back to section prompts
            literature = []
            self.literature_warning = f"literature retrieval skipped: {error}"
            print(self.literature_warning, flush=True)
        else:
            self.literature_warning = ""
            print(f"literature hits: {len(literature)}", flush=True)
        react_payload: dict = {"enabled": False, "trajectory": None}

        if self.react_enabled and not self.literature_document_ids:
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

        display_labels: dict[str, str] = {}
        written: dict[str, str] = {}
        catalog_items = catalog_from_raw(analysis.raw if isinstance(analysis.raw, dict) else None)
        # Material packages always go through the core skills: numbers are
        # computed in code and Methods come from recorded provenance.
        ledger: dict | None = None
        methods_skill: dict | None = None
        self.core_skill_warnings: list[str] = []
        if material_report and material_report.get("package_root"):
            try:
                ledger = bio_skills.build_ledger(material_report["package_root"])
                methods_skill = bio_skills.build_methods(ledger)
                catalog_items = [*catalog_items, bio_skills.ledger_catalog_item(ledger)]
                print(f"fact ledger: {len(ledger.get('facts', []))} facts", flush=True)
            except Exception as error:  # noqa: BLE001 — fall back to the material facts
                ledger = methods_skill = None
                self.core_skill_warnings.append(f"core writing skills unavailable: {error}")
                print(self.core_skill_warnings[-1], flush=True)
        # The author's chosen papers become citable evidence cards (abstracts fetched by DOI).
        reference_cards: list[dict] = []
        refs = [*self.literature_refs, *bio_skills.refs_from_hits(literature if self.literature_document_ids else [])]
        if ledger is not None and refs:
            try:
                built = bio_skills.build_reference_cards(
                    refs, model=self.llm if getattr(self.llm, "enabled", False) else None)
                reference_cards = built.get("cards", [])
                for item in built.get("skipped", []):
                    self.core_skill_warnings.append(f"reference skipped: {item}")
                print(f"reference cards: {len(reference_cards)}", flush=True)
            except Exception as error:  # noqa: BLE001 — write without citations rather than fail
                self.core_skill_warnings.append(f"reference cards unavailable: {error}")
                print(self.core_skill_warnings[-1], flush=True)
        self.reference_cards = reference_cards
        # Figures go to Technical Validation in a Data Descriptor, otherwise to Results.
        figures, figure_warnings = figure_source.resolve_figures(self.figure_ids)
        self.figures = figures
        if figures and ledger is not None:
            # The package had no figures, but the author supplied chart-module figures.
            ledger["missing"] = [m for m in ledger.get("missing", []) if m.get("id") != "figures"]
        self.core_skill_warnings.extend(figure_warnings)
        chosen_ids = {s.value for s in self.section_order}
        figure_section = "technical_validation" if "technical_validation" in chosen_ids else "results"
        if figures and figure_section not in chosen_ids:
            self.core_skill_warnings.append("figures not placed: choose a Results or Technical Validation section")
        connected = bool(
            getattr(self.llm, "enabled", False)
            and not isinstance(self.llm, LLMDisabled)
            and catalog_items
        )
        # Display order is the user's; writing order follows the library's write_rank
        # (data-bearing sections first, summaries last).
        order = (
            [SectionId(s) for s in section_library.writing_order(self.section_order)]
            if connected
            else list(self.section_order)
        )
        for index, section_id in enumerate(order, start=1):
            if self.on_progress:
                self.on_progress(section_id.value, index, len(order))
            print(
                f"[{index}/{len(order)}] writing {section_id.value}…",
                flush=True,
            )
            if section_id.value not in section_library.LEGACY_IDS:
                draft = library_sections.write_library_section(
                    section_library.get(section_id.value), section_id,
                    ledger=ledger, cards=reference_cards, brief=brief,
                    model=self.llm if connected else None, written=written,
                    material_report=material_report,
                    figures=figures if section_id.value == figure_section else [],
                    style_rules=self.llm_instructions)
                draft.warnings.extend(self.core_skill_warnings)
                state.add_draft(draft)
                written[section_id.value] = draft.markdown
                print(f"[{index}/{len(order)}] {section_id.value} done "
                      f"status={draft.metadata['library']['status']}", flush=True)
                continue
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
            # Connected drafting keeps each chapter to its own facts. The old
            # per-section form prompts (ethics slots, abbreviation lists) stay
            # off that path. They remain for a template-only run.
            extra = self.llm_instructions
            skill_meta: dict = {
                "enabled": self.skills_enabled,
                "selected": sorted(self.selected_skills),
                "writing": False,
                "polishing": False,
            }
            if not connected:
                sec_extra = (draft.metadata or {}).get("llm_instructions_extra") or ""
                if sec_extra:
                    extra = f"{extra}\n\n{sec_extra}".strip() if extra else str(sec_extra)
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
            instructions = self.llm_instructions
            if connected and WRITING_SKILL in self.selected_skills:
                write_rules, write_handoff = writing_instructions(
                    self.skill_client,
                    section=section_id,
                    brief=brief,
                    analysis=analysis,
                )
                if write_rules:
                    instructions = (
                        f"{instructions}\n\n{write_rules}".strip() if instructions else write_rules
                    )
                skill_meta["writing"] = bool(write_handoff)
                if write_handoff:
                    skill_meta["playbook_chars"] = len(write_rules)
            if connected and POLISH_SKILL in self.selected_skills:
                polish_rules, polish_handoff = polish_instructions(
                    self.skill_client, section=section_id, draft_text=draft.markdown,
                )
                if polish_rules:
                    instructions = f"{instructions}\n\n{polish_rules}".strip()
                skill_meta["polishing"] = bool(polish_rules)
                skill_meta["polishing_mode"] = "inline_generation"
                skill_meta["playbook_chars"] = len(instructions)
            if ledger is not None:
                skill_meta["core"] = bio_skills.skills_metadata(
                    ledger, methods=methods_skill if section_id == SectionId.methods else None)
            if connected and methods_skill is not None and section_id == SectionId.methods:
                meta = dict(draft.metadata or {})
                meta["catalog_compose"] = {"used": False, "source": bio_skills.METHODS_SKILL}
                draft = draft.model_copy(update={
                    "markdown": bio_skills.methods_markdown(methods_skill),
                    "metadata": meta,
                    "figure_refs": [],
                })
                written[section_id.value] = draft.markdown
            elif connected and ledger is not None and section_id in section_runner.PLANNED_SECTIONS:
                def paragraph_progress(key: str, step: int, total: int, _section=section_id.value) -> None:
                    print(f"    {_section} paragraph {step}/{total}: {key}", flush=True)

                markdown, planned_meta, planned_warnings = section_runner.write_planned_section(
                    section_id, ledger, self.llm, brief, style_rules=instructions, cards=reference_cards,
                    figures=figures if section_id.value == figure_section else [],
                    on_paragraph=paragraph_progress)
                planned_refs = planned_meta.pop("_figure_refs", [])
                planned_meta["style_rules_chars"] = len(instructions or "")
                meta = dict(draft.metadata or {})
                meta["catalog_compose"] = {"used": False, "source": "planned_paragraphs"}
                meta["planned"] = planned_meta
                draft = draft.model_copy(update={
                    "markdown": markdown,
                    "metadata": meta,
                    "figure_refs": list(planned_refs),
                    "warnings": [*draft.warnings, *planned_warnings],
                })
                written[section_id.value] = draft.markdown
            elif connected:
                draft = compose_section_from_catalog(
                    draft,
                    self.llm,
                    catalog=catalog_items,
                    brief=brief,
                    literature=literature,
                    language=self.llm_language,
                    instructions=instructions,
                    strict=True,
                    known_labels=display_labels or None,
                    facts=(bio_skills.section_facts(ledger, section_id) if ledger is not None
                           else material_instructions(material_report, section_id.value)
                           if material_report else facts_for_section(section_id, catalog_items)),
                    prior_prose=prior_prose_for(section_id, written),
                    translate=False,
                )
                written[section_id.value] = draft.markdown
                if section_id == SectionId.results:
                    found = (draft.metadata or {}).get("display_labels") or {}
                    if isinstance(found, dict) and found:
                        display_labels = {str(key): str(value) for key, value in found.items()}
            elif getattr(self.llm, "enabled", False) and not isinstance(self.llm, LLMDisabled):
                draft = polish_section_draft(
                    draft,
                    self.llm,
                    language=self.llm_language,
                    instructions=extra,
                    paper_context="",
                )
            elif extra and self.skills_enabled:
                draft.warnings.append(
                    f"skills playbook attached for {section_id.value}; "
                    "enable --llm to execute drafting/polish with the local model"
                )
            if self.literature_warning:
                draft.warnings.append(self.literature_warning)
            draft.warnings.extend(self.core_skill_warnings)
            meta = dict(draft.metadata or {})
            meta["skills"] = skill_meta
            if material_report:
                meta["material_check"] = material_report
                draft.warnings.append("描述性初稿：研究设计及统计证据尚未确认，请核对材料检查。")
            draft = draft.model_copy(update={"metadata": meta})
            draft = apply_evidence_binding(
                draft,
                section_input=section_input,
                state=state,
                mode=self.evidence_mode,
            )
            state.add_draft(draft)
            composed = (draft.metadata or {}).get("catalog_compose") or {}
            print(
                f"[{index}/{len(order)}] {section_id.value} done"
                f" compose={composed.get('used', False)} warnings={len(draft.warnings)}",
                flush=True,
            )

        if connected and ledger is not None:
            self._quality_gates(state, ledger)

        if connected and "zh" in self.llm_language.lower():
            if self.on_progress:
                self.on_progress("translation", 0, 0)
            print("translating chapters…", flush=True)
            for section_id in self.section_order:
                current = state.drafts.get(section_id.value)
                if current is None:
                    continue
                if (current.metadata or {}).get("library", {}).get("status") in {"skill_missing", "author_input"}:
                    continue  # placeholders stay as written
                translated = translate_section_text(self.llm, current.markdown, self.llm_language)
                if translated != current.markdown:
                    state.add_draft(current.model_copy(update={"markdown": translated}))

        if reference_cards:
            self._attach_references(state, reference_cards)

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

    MODEL_WRITTEN_SKILLS = frozenset({"background-summary-writer", "technical-validation-writer"})

    def _quality_gates(self, state: PaperState, ledger: dict | None = None) -> None:
        """Mock review → targeted revision on the English text, then the STORMS-informed check."""

        bio_skills.ensure_skills_path()
        drafts = {s.value: state.drafts[s.value] for s in self.section_order if s.value in state.drafts}
        sections = {k: d.markdown for k, d in drafts.items()}
        titles = {k: d.title for k, d in drafts.items()}
        revisable = {k for k, d in drafts.items()
                     if "planned" in (d.metadata or {})
                     or (d.metadata or {}).get("library", {}).get("skill") in self.MODEL_WRITTEN_SKILLS}
        self.quality_report = {}
        if self.on_progress:
            self.on_progress("review", 0, 0)
        print("reviewing and revising…", flush=True)
        try:
            outcome = bio_skills._run("targeted-reviser", {"sections": sections, "titles": titles,
                                                           "revisable": sorted(revisable), "model": self.llm,
                                                           "ledger": ledger or {}})
        except Exception as error:  # noqa: BLE001 — review is a quality gate, not a blocker
            self.core_skill_warnings.append(f"review skipped: {error}")
            outcome = None
        if outcome:
            for key, draft in drafts.items():
                issues = [i for i in outcome["issues"] if i["section"] == key]
                if not issues and outcome["sections"].get(key) == draft.markdown:
                    continue
                meta = dict(draft.metadata or {})
                meta["review"] = [{k: i.get(k) for k in ("severity", "kind", "quote", "problem", "action")}
                                  for i in issues]
                notes = [f"review ({i['severity']}): {i['problem']}" for i in issues if i["action"] != "revised"]
                updated = draft.model_copy(update={"markdown": outcome["sections"].get(key, draft.markdown),
                                                   "metadata": meta, "warnings": [*draft.warnings, *notes]})
                state.add_draft(updated)
                drafts[key] = updated
            self.skill_outputs["peer_review.md"] = outcome["markdown"]
            self.quality_report["review"] = {
                "issues": len(outcome["issues"]), "discarded": len(outcome["discarded"]),
                "revised": sum(1 for i in outcome["issues"] if i["action"] == "revised")}
        storms = bio_skills._run("storms-checklist", {"sections": {k: d.markdown for k, d in drafts.items()}})
        self.skill_outputs["storms_checklist.md"] = storms["markdown"]
        self.quality_report["storms"] = storms["counts"]
        print(f"quality gates: {self.quality_report}", flush=True)

    def _attach_references(self, state: PaperState, cards: list[dict]) -> None:
        """Reference list in citation order, appended to the back matter as `[@key]: entry` lines."""

        from article_writing.contracts import Citation

        citation_keys = bio_skills.citation_keys
        order: list[str] = []
        for section_id in self.section_order:
            draft = state.drafts.get(section_id.value)
            if draft is None:
                continue
            for key in citation_keys(draft.markdown.split("\n中文\n")[0]):
                if key not in order:
                    order.append(key)
        by_key = {card["key"]: card for card in cards}
        order = [key for key in order if key in by_key]
        # The reference list goes to the back matter, or to the last chosen section without it.
        target = state.drafts.get(SectionId.back_matter.value)
        if target is None:
            for section_id in reversed(self.section_order):
                if section_id.value in state.drafts:
                    target = state.drafts[section_id.value]
                    break
        if not order or target is None:
            return
        block = bio_skills.references_block(cards, order)
        citations = [Citation(cite_id=key, title=by_key[key]["title"], authors=", ".join(by_key[key]["authors"]),
                              year=int(by_key[key]["year"]) if str(by_key[key]["year"]).isdigit() else None,
                              doi=by_key[key]["doi"] or None, pmid=by_key[key].get("pmid") or None,
                              snippet=by_key[key].get("abstract", "")[:400], source=by_key[key].get("source", ""))
                     for key in order]
        meta = dict(target.metadata or {})
        meta["references"] = [{"key": key, "doi": by_key[key]["doi"], "entry": bio_skills.reference_entry(by_key[key])}
                              for key in order]
        state.add_draft(target.model_copy(update={
            "markdown": bio_skills.add_references(target.markdown, block),
            "citations": [*target.citations, *citations],
            "metadata": meta,
        }))

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
