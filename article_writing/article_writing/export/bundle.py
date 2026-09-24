"""Export WritingBundle, section markdown, figures, and compiled manuscript."""

from __future__ import annotations

import json
from pathlib import Path

from article_writing.consistency.report import ConsistencyReport
from article_writing.contracts import PaperState, SectionId, WritingBundle
from article_writing.layout.figures import stage_figures
from article_writing.layout.manuscript import build_manuscript_markdown


def build_bundle(
    state: PaperState,
    *,
    consistency_report: ConsistencyReport | None = None,
) -> WritingBundle:
    sections = [state.drafts[sid.value] for sid in state.section_order if sid.value in state.drafts]
    warnings: list[str] = []
    for draft in sections:
        warnings.extend(draft.warnings)
    if consistency_report is not None:
        for message in consistency_report.warning_messages():
            if message not in warnings:
                warnings.append(message)
    return WritingBundle(
        run_id=state.run_id,
        brief=state.brief,
        sections=sections,
        claims=state.confirmed_claims,
        citations=state.citations,
        figure_refs=state.figure_refs,
        warnings=warnings,
    )


def _export_react_trajectory(state: PaperState, output_dir: Path) -> Path | None:
    for section_key in (SectionId.introduction.value, SectionId.discussion.value):
        draft = state.drafts.get(section_key)
        if draft is None:
            continue
        react_meta = (draft.metadata or {}).get("react") or {}
        trajectory = react_meta.get("trajectory")
        if react_meta.get("enabled") and trajectory:
            path = output_dir / "react_trajectory.json"
            path.write_text(
                json.dumps(trajectory, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return path
    return None


def _export_title_page(state: PaperState, output_dir: Path) -> Path:
    brief = state.brief
    authors = brief.authors or "Authors not supplied"
    lines = [
        f"# {brief.title or 'Untitled manuscript'}",
        "",
        f"**Authors / 作者:** {authors}",
        "",
        f"**Run ID:** {state.run_id}",
        "",
    ]
    if brief.research_question:
        lines.extend(
            [
                f"**Research question / 研究问题:** {brief.research_question}",
                "",
            ]
        )
    path = output_dir / "title_page.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _export_consistency_report(
    report: ConsistencyReport | None,
    output_dir: Path,
) -> Path | None:
    if report is None:
        return None
    path = output_dir / "consistency_report.json"
    path.write_text(
        json.dumps(report.as_metadata(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def export_bundle(
    state: PaperState,
    output_dir: Path | str,
    *,
    consistency_report: ConsistencyReport | None = None,
    figure_search_roots: list[Path] | None = None,
    skill_outputs: dict[str, str] | None = None,
) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    sections_dir = out / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)

    roots = list(figure_search_roots or [])
    # Always search CWD and common project root guesses.
    roots.extend([Path.cwd(), Path.cwd() / "fixtures", out])
    copied, missing = stage_figures(
        list(state.figure_refs),
        out,
        search_roots=roots,
    )

    bundle = build_bundle(state, consistency_report=consistency_report)
    if missing:
        for mid in missing:
            note = f"figure asset missing: {mid}"
            if note not in bundle.warnings:
                bundle.warnings.append(note)

    bundle_path = out / "writing_bundle.json"
    bundle_path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

    state_path = out / "paper_state.json"
    state_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")

    for draft in bundle.sections:
        md_path = sections_dir / f"{draft.section.value}.md"
        md_path.write_text(draft.markdown, encoding="utf-8")

    manuscript = build_manuscript_markdown(state)
    (out / "manuscript.md").write_text(manuscript, encoding="utf-8")

    layout_meta = {
        "figures_copied": copied,
        "figures_missing": missing,
        "manuscript": "manuscript.md",
    }
    (out / "layout_report.json").write_text(
        json.dumps(layout_meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    _export_title_page(state, out)
    _export_react_trajectory(state, out)
    _export_consistency_report(consistency_report, out)
    for name, text in (skill_outputs or {}).items():
        if not name.endswith(".md") or not text.strip():
            continue
        (out / name).write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return bundle_path
