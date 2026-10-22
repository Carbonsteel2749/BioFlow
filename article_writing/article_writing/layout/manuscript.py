"""Assemble a journal-style bilingual manuscript from PaperState."""

from __future__ import annotations

import re

from article_writing.contracts import PaperState, SectionId
from article_writing.layout.bilingual import select_language
from article_writing.sections.text_utils import clean_text

# Catalog ids are for the writer and the file linker. Readers see Figure / Table.
_CAPTION_ID = re.compile(
    r"(\*\*(?:Figure|Table) \d+\.\*\*[^\n]*?)\s*\(`(cat_[A-Za-z0-9_]+)`\)"
)
_CATALOG_TOKEN = re.compile(r"`(cat_[A-Za-z0-9_]+)`|(?<![\w/])(cat_[A-Za-z0-9_]+)")
_EMPTY_PAREN = re.compile(r"[ \t]*[（(]\s*[)）]")
_EMPTY_LIST = re.compile(r"[ \t]*[（(]\s*(?:[，,、]\s*)+[)）]")
_REPEAT_COMMA = re.compile(r"(?:,\s*){2,}")
_REPEAT_IDEO = re.compile(r"(?:，\s*){2,}")
_DANGLING_CONJUNCTION = re.compile(r"\s+(?:and|or|及|和)\s*([.。,，;；])")
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:，。；])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")


SECTION_TITLES_ZH = {
    SectionId.abstract: "摘要",
    SectionId.introduction: "引言",
    SectionId.methods: "方法",
    SectionId.results: "结果",
    SectionId.discussion: "讨论",
    SectionId.conclusion: "结论",
    SectionId.back_matter: "文后事项",
}


def hide_catalog_ids(text: str, labels: dict[str, str] | None = None) -> str:
    """Replace catalog ids in reader text with Figure / Table labels.

    Image and table file paths stay intact so the export can still find the
    asset. Ids that are not a figure or a table are removed.
    """

    found = dict(labels or {})

    def _remember_caption(match: re.Match[str]) -> str:
        heading = re.search(r"(Figure|Table) \d+", match.group(1))
        if heading:
            found[match.group(2)] = heading.group(0)
        return match.group(1)

    cleaned = _CAPTION_ID.sub(_remember_caption, text or "")

    def _replace_token(match: re.Match[str]) -> str:
        item_id = match.group(1) or match.group(2)
        if item_id in found:
            return found[item_id]
        best_key = ""
        best_label = ""
        for key, label in found.items():
            if len(key) < 12:
                continue
            if item_id.startswith(key) or key.startswith(item_id):
                if len(key) > len(best_key):
                    best_key = key
                    best_label = label
        return best_label

    cleaned = _CATALOG_TOKEN.sub(_replace_token, cleaned)
    cleaned = _EMPTY_LIST.sub("", cleaned)
    cleaned = _EMPTY_PAREN.sub("", cleaned)
    cleaned = _DANGLING_CONJUNCTION.sub(r"\1", cleaned)
    cleaned = _REPEAT_COMMA.sub(", ", cleaned)
    cleaned = _REPEAT_IDEO.sub("，", cleaned)
    cleaned = _SPACE_BEFORE_PUNCT.sub(r"\1", cleaned)
    cleaned = _MULTI_SPACE.sub(" ", cleaned)
    return cleaned


def reader_title_lines(title: str, authors: str = "") -> list[str]:
    """Title block a reader sees. Run id and the study question stay in the session."""

    lines = [f"# {clean_text(title) or 'Untitled manuscript'}", ""]
    author_line = clean_text(authors)
    if author_line:
        lines.extend([f"**Authors / 作者:** {author_line}", ""])
    return lines


def build_manuscript_markdown(state: PaperState, *, language: str = "both") -> str:
    """Concatenate title page + sections into one layout-ready Markdown file.

    ``language`` is ``both``, ``en``, or ``zh``. A single-language export keeps
    figures and tables with the chosen prose.
    """

    choice = (language or "both").strip().lower()
    lines = reader_title_lines(state.brief.title, state.brief.authors)
    lines.extend(["---", ""])

    for section_id in state.section_order:
        draft = state.drafts.get(section_id.value)
        if draft is None:
            continue
        zh = SECTION_TITLES_ZH.get(section_id, section_id.value)
        if choice == "zh":
            lines.append(f"# {zh}")
        elif choice == "en":
            lines.append(f"# {draft.title}")
        else:
            lines.append(f"# {draft.title} / {zh}")
        lines.append("")
        body = select_language(draft.markdown or "", choice).strip()
        if choice == "both" and body.startswith("# "):
            body_lines = body.splitlines()
            body = "\n".join(body_lines[1:]).lstrip("\n")
        lines.append(hide_catalog_ids(body).rstrip())
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
