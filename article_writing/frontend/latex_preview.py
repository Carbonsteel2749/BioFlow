"""Turn a writing session into XeLaTeX and compile a PDF beside the source."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from article_writing.layout.bilingual import select_language, split_bilingual
from frontend.session_store import WritingSession

_IMAGE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<path>figures/[^)\s]+)\)"
    r"(?:\s*\n+\*\*(?P<label>(?:Figure|Table)\s+\d+\.)\*\*\s*(?P<caption>[^\n]*))?",
    re.IGNORECASE,
)
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf"}
_ESCAPE = str.maketrans(
    {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
)


def _escape(text: str) -> str:
    return text.translate(_ESCAPE)


def _text_block(text: str) -> str:
    body = text.strip()
    if not body:
        return ""
    pieces: list[str] = []
    cursor = 0
    for match in _BOLD.finditer(body):
        pieces.append(_escape(body[cursor : match.start()]))
        pieces.append(r"\textbf{" + _escape(match.group(1)) + "}")
        cursor = match.end()
    pieces.append(_escape(body[cursor:]))
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", "".join(pieces)) if part.strip()]
    return "\n\n".join(paragraphs)


def _figure(match: re.Match[str], figures: Path) -> str:
    filename = Path(match.group("path")).name
    if not _SAFE_NAME.fullmatch(filename) or Path(filename).suffix.lower() not in _IMAGE_SUFFIXES:
        return ""
    if not (figures / filename).is_file():
        return ""
    label = (match.group("label") or "").strip()
    caption = (match.group("caption") or match.group("alt") or filename).strip()
    shown = " ".join(part for part in (label, caption) if part)
    return (
        "\\begin{figure}[ht]\n\\centering\n"
        f"\\includegraphics[width=0.92\\linewidth]{{{filename}}}\n"
        f"\\caption{{{_escape(shown)}}}\n"
        "\\end{figure}"
    )


def markdown_to_latex(markdown: str, figures: Path) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _IMAGE.finditer(markdown or ""):
        parts.append(_text_block((markdown or "")[cursor : match.start()]))
        parts.append(_figure(match, figures))
        cursor = match.end()
    parts.append(_text_block((markdown or "")[cursor:]))
    return "\n\n".join(part for part in parts if part and part.strip())


def _has_cjk(text: str) -> bool:
    return len(_CJK.findall(text or "")) >= 8


def _document_title(session: WritingSession, language: str) -> str:
    title = (session.title or "").strip() or "Manuscript"
    if language != "en" or not _has_cjk(title):
        return title
    abstract = session.sections.get("abstract")
    english = ""
    if abstract is not None:
        english, _chinese, _shared = split_bilingual(abstract.markdown or "")
    sentence = re.split(r"[.。]", english.strip(), maxsplit=1)[0].strip()
    if not sentence:
        return title
    if len(sentence) > 140:
        shortened = sentence[:140]
        if " " in shortened:
            shortened = shortened.rsplit(" ", 1)[0]
        sentence = shortened
    return sentence


def language_choice(language: str) -> str:
    return "en" if (language or "").strip().lower() == "en" else "zh"


def section_bodies(session: WritingSession, language: str) -> list[tuple[str, str, str]]:
    """Return section id, heading, and LaTeX body for the chosen language."""

    choice = language_choice(language)
    figures = Path(session.output_dir) / "pipeline" / "figures"
    rows: list[tuple[str, str, str]] = []
    for key in session.section_order:
        section = session.sections.get(key)
        if section is None or not (section.markdown or "").strip():
            continue
        heading = section.title if choice == "en" else (section.title_zh or section.title or key)
        body = markdown_to_latex(select_language(section.markdown, choice), figures)
        if not body.strip():
            continue
        rows.append((key, heading, body))
    return rows


def manuscript_opening(session: WritingSession, language: str) -> str:
    choice = language_choice(language)
    question = (session.research_question or "").strip()
    if choice == "en" and _has_cjk(question):
        question = ""
    opening = "\\maketitle\n"
    if question:
        opening += "\n" + _escape(question) + "\n"
    return opening


def manuscript_sections(session: WritingSession, language: str) -> str:
    parts = [
        "\\section{" + _escape(heading) + "}\n\n" + body
        for _key, heading, body in section_bodies(session, language)
    ]
    return "\n\n".join(parts)


def session_to_latex(session: WritingSession, language: str = "zh") -> str:
    title = _escape(_document_title(session, language))
    return (
        "\\documentclass[UTF8,fontset=fandol]{ctexart}\n"
        "\\usepackage{graphicx}\n"
        "\\graphicspath{{figures/}}\n"
        f"\\title{{{title}}}\n"
        "\\date{}\n"
        "\\begin{document}\n"
        f"{manuscript_opening(session, language)}\n"
        + manuscript_sections(session, language)
        + "\n\\end{document}\n"
    )


def latex_dir(session: WritingSession) -> Path:
    return Path(session.output_dir) / "pipeline"


def compile_latex(session: WritingSession, tex: str, support_dir: Path | None = None) -> tuple[Path | None, str]:
    work = latex_dir(session)
    work.mkdir(parents=True, exist_ok=True)
    if support_dir is not None and support_dir.is_dir():
        for item in support_dir.rglob("*"):
            if not item.is_file() or item.name == "template.json":
                continue
            relative = item.relative_to(support_dir)
            if ".." in relative.parts or relative.name == "manuscript.tex":
                continue
            dest = work / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(item.read_bytes())
    source = work / "manuscript.tex"
    source.write_text(tex, encoding="utf-8")
    try:
        completed = subprocess.run(
            [
                "xelatex",
                "-no-shell-escape",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "manuscript.tex",
            ],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "编译超过 180 秒，已停止。"
    except OSError as exc:
        return None, f"无法启动 XeLaTeX：{exc}"
    pdf = work / "manuscript.pdf"
    log = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    if completed.returncode != 0 or not pdf.is_file():
        tail = "\n".join(log.splitlines()[-25:])
        return None, tail or "XeLaTeX 没有生成 PDF。"
    return pdf, ""
