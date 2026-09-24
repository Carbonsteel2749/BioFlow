"""Assemble a journal-style bilingual manuscript from PaperState."""

from __future__ import annotations

from article_writing.contracts import PaperState, SectionId
from article_writing.sections.text_utils import clean_text


SECTION_TITLES_ZH = {
    SectionId.abstract: "摘要",
    SectionId.introduction: "引言",
    SectionId.methods: "方法",
    SectionId.results: "结果",
    SectionId.discussion: "讨论",
    SectionId.conclusion: "结论",
    SectionId.back_matter: "文后事项",
}


def build_manuscript_markdown(state: PaperState) -> str:
    """Concatenate title page + sections into one layout-ready Markdown file."""

    brief = state.brief
    title = clean_text(brief.title) or "Untitled manuscript"
    authors = clean_text(brief.authors) or "Authors not supplied"
    lines: list[str] = [
        f"# {title}",
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
    lines.extend(
        [
            "> Layout note: English and Chinese bodies are required. "
            "When LLM polish is enabled, each section should contain both languages. "
            "Numbers, metrics, conclusions, and figure paths must remain unchanged "
            "from upstream analysis/literature modules.",
            "",
            "---",
            "",
        ]
    )

    for section_id in state.section_order:
        draft = state.drafts.get(section_id.value)
        if draft is None:
            continue
        zh = SECTION_TITLES_ZH.get(section_id, section_id.value)
        lines.append(f"# {draft.title} / {zh}")
        lines.append("")
        body = (draft.markdown or "").strip()
        # Avoid duplicated top-level H1 if section already starts with "# Title"
        if body.startswith("# "):
            body_lines = body.splitlines()
            body = "\n".join(body_lines[1:]).lstrip("\n")
        lines.append(body.rstrip())
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
