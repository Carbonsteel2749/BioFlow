"""Write sections that come from the section library (not the seven legacy sections).

* mode ``skill``: run the section's registered skill (contract in SECTION_SKILLS.md);
  if the skill is not registered yet, keep a visible placeholder so the manuscript
  structure is complete and the gap is obvious.
* mode ``author_input``: information the system cannot know; never generated.
"""

from __future__ import annotations

from typing import Any

from article_writing.contracts import FigureRef, SectionDraft, SectionId
from article_writing.skills.bio_skills import ensure_skills_path

SKILL_PENDING = "SKILL_PENDING"
AUTHOR_INPUT = "AUTHOR_INPUT_NEEDED"


def skill_registered(name: str | None) -> bool:
    if not name:
        return False
    ensure_skills_path()
    try:
        from bioflow_skills.registry import get_registry
        return get_registry().has(name)
    except Exception:  # noqa: BLE001
        return False


def skill_payload(spec: Any, *, ledger, cards, brief, model, written, material_report,
                  figures=None, style_rules: str = "") -> dict[str, Any]:
    """The fixed payload every section skill receives (see SECTION_SKILLS.md)."""

    return {
        "figures": list(figures or []),
        "style_rules": style_rules or "",
        "section": spec.id,
        "section_spec": spec.as_dict(),
        "brief": {"title": str(getattr(brief, "title", "") or ""),
                  "research_question": str(getattr(brief, "research_question", "") or "")},
        "ledger": ledger or {},
        "cards": cards or [],
        "materials": material_report or {},
        "written": dict(written or {}),
        "model": model,
    }


def _draft(spec, section_id: SectionId, markdown: str, status: str, warnings: list[str],
           extra: dict[str, Any] | None = None) -> SectionDraft:
    meta = {"library": {"status": status, "mode": spec.mode, "skill": spec.skill, **(extra or {})},
            "catalog_compose": {"used": False, "source": f"library:{status}"}}
    return SectionDraft(section=section_id, title=spec.title_en, markdown=markdown if markdown.endswith("\n")
                        else markdown + "\n", warnings=warnings, metadata=meta)


def write_library_section(spec, section_id: SectionId, *, ledger=None, cards=None, brief=None, model=None,
                          written=None, material_report=None, figures=None, style_rules: str = "",
                          author_information=None) -> SectionDraft:
    if spec.mode == "author_input":
        supplied = str((author_information or {}).get(spec.id) or '').strip()
        if supplied:
            return _draft(spec, section_id, supplied, 'author_provided',
                          [f'{spec.id}: 作者提供，请在投稿前确认。'], {'origin':'author_input'})
        text = f"[{AUTHOR_INPUT}: {spec.title_en} — {spec.purpose}]"
        return _draft(spec, section_id, text, "author_input",
                      [f"{spec.id}: author input required ({spec.title_zh})"])
    if not skill_registered(spec.skill):
        text = (f"[{SKILL_PENDING}: {spec.skill or 'no skill assigned'}] {spec.title_en}: {spec.purpose}")
        return _draft(spec, section_id, text, "skill_missing",
                      [f"{spec.id}: skill '{spec.skill}' is not registered yet; placeholder kept ({spec.title_zh})"])
    from bioflow_skills import run_skill

    payload = skill_payload(spec, ledger=ledger, cards=cards, brief=brief, model=model, written=written,
                            material_report=material_report, figures=figures, style_rules=style_rules)
    result = run_skill(spec.skill, payload)
    data = getattr(result, "data", None) or {}
    markdown = str(data.get("markdown") or "").strip()
    if not getattr(result, "succeeded", False) or not markdown:
        reason = getattr(result, "error", "") or "skill returned no markdown"
        text = f"[{SKILL_PENDING}: {spec.skill} failed] {spec.title_en}: {spec.purpose}"
        return _draft(spec, section_id, text, "skill_failed", [f"{spec.id}: skill '{spec.skill}' failed: {reason}"])
    used = {str(f.get("figure_id")) for f in data.get("figures") or [] if isinstance(f, dict)}
    by_id = {f["figure_id"]: f for f in figures or []}
    draft = _draft(spec, section_id, markdown, "written", [str(w) for w in data.get("warnings") or []],
                   {"citations": list(data.get("citations") or []), "version": getattr(result, "version", ""),
                    "figures": [dict(f) for f in data.get("figures") or [] if isinstance(f, dict)]})
    refs = [FigureRef(figure_id=fid, path=by_id[fid]["path"], caption=by_id[fid]["caption"], kind="figure")
            for fid in by_id if fid in used]
    return draft.model_copy(update={"figure_refs": refs})
