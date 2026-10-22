"""Export final manuscript to Word (.docx) and PDF via HTML + LibreOffice."""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from pathlib import Path


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}  # SVG often fails in LO embed
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")


def _inline_md_to_html(text: str) -> str:
    text = html.escape(text)
    # restore intentional markers after escape by working on raw then escaping pieces
    return text


def _format_inline(raw: str) -> str:
    """Escape + apply simple bold/code/link markdown."""

    # links first on raw
    parts: list[str] = []
    pos = 0
    for match in re.finditer(r"\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)", raw):
        if match.start() > pos:
            parts.append(html.escape(raw[pos : match.start()]))
        token = match.group(0)
        if token.startswith("**"):
            parts.append(f"<strong>{html.escape(token[2:-2])}</strong>")
        elif token.startswith("`"):
            parts.append(f"<code>{html.escape(token[1:-1])}</code>")
        else:
            m = _LINK_RE.match(token)
            if m:
                parts.append(
                    f'<a href="{html.escape(m.group(2))}">{html.escape(m.group(1))}</a>'
                )
            else:
                parts.append(html.escape(token))
        pos = match.end()
    if pos < len(raw):
        parts.append(html.escape(raw[pos:]))
    return "".join(parts)


def _resolve_image(path_str: str, search_roots: list[Path]) -> Path | None:
    raw = Path(path_str)
    candidates = [raw] if raw.is_absolute() else []
    for root in search_roots:
        candidates.extend(
            [
                root / path_str,
                root / raw.name,
                root / "figures" / raw.name,
                root / "pipeline" / "figures" / raw.name,
            ]
        )
    for path in candidates:
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            return path.resolve()
    return None


def markdown_to_html(
    markdown: str,
    *,
    title: str = "",
    search_roots: list[Path] | None = None,
) -> str:
    roots = list(search_roots or [])
    body: list[str] = []
    lines = markdown.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        if stripped == "---":
            body.append("<hr />")
            i += 1
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            level = min(len(heading.group(1)), 3)
            text = heading.group(2).strip()
            if level == 1 and title and text == title:
                i += 1
                continue
            body.append(f"<h{level}>{_format_inline(text)}</h{level}>")
            i += 1
            continue
        image = _IMAGE_RE.search(stripped)
        if image:
            alt = image.group(1).strip() or "Figure"
            img = _resolve_image(image.group(2).strip(), roots)
            if img is not None:
                body.append(
                    "<figure>"
                    f'<img src="{html.escape(img.as_uri())}" alt="{html.escape(alt)}" />'
                    f"<figcaption>{html.escape(alt)}</figcaption>"
                    "</figure>"
                )
            else:
                body.append(
                    f"<p><em>[Figure: {html.escape(alt)} — "
                    f"{html.escape(image.group(2))}]</em></p>"
                )
            i += 1
            continue
        if stripped.startswith(("- ", "* ")):
            body.append("<ul>")
            while i < len(lines) and lines[i].strip().startswith(("- ", "* ")):
                item = lines[i].strip()[2:].strip()
                body.append(f"<li>{_format_inline(item)}</li>")
                i += 1
            body.append("</ul>")
            continue
        if re.match(r"^\d+\.\s+", stripped):
            body.append("<ol>")
            while i < len(lines) and re.match(r"^\d+\.\s+", lines[i].strip()):
                item = re.sub(r"^\d+\.\s+", "", lines[i].strip())
                body.append(f"<li>{_format_inline(item)}</li>")
                i += 1
            body.append("</ol>")
            continue
        if stripped.startswith(">"):
            body.append(f"<blockquote>{_format_inline(stripped.lstrip('> ').strip())}</blockquote>")
            i += 1
            continue
        chunk = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (
                not nxt
                or nxt == "---"
                or _HEADING_RE.match(nxt)
                or nxt.startswith(("- ", "* ", ">"))
                or _IMAGE_RE.search(nxt)
                or re.match(r"^\d+\.\s+", nxt)
            ):
                break
            chunk.append(nxt)
            i += 1
        body.append(f"<p>{_format_inline(' '.join(chunk))}</p>")

    title_html = html.escape(title or "Manuscript")
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>{title_html}</title>
  <style>
    body {{
      font-family: "Noto Sans CJK SC", "Noto Serif CJK SC", "Droid Sans Fallback",
                   "DejaVu Sans", sans-serif;
      font-size: 11pt;
      line-height: 1.55;
      color: #122017;
      margin: 2.2cm;
    }}
    h1, h2, h3 {{ color: #14532d; }}
    h1 {{ font-size: 20pt; }}
    h2 {{ font-size: 15pt; margin-top: 1.2em; }}
    h3 {{ font-size: 12.5pt; }}
    code {{ font-family: "Noto Sans Mono CJK SC", Consolas, monospace; font-size: 0.92em; }}
    figure {{ text-align: center; margin: 1.2em 0; }}
    img {{ max-width: 100%; height: auto; }}
    figcaption {{ font-size: 9pt; font-style: italic; color: #4b6354; }}
    blockquote {{ color: #4b6354; border-left: 3px solid #16a34a; padding-left: 0.8em; }}
    hr {{ border: 0; border-top: 1px solid #c9e2d2; margin: 1.4em 0; }}
  </style>
</head>
<body>
  <h1>{title_html}</h1>
  {''.join(body)}
</body>
</html>
"""


def _libreoffice_convert(source: Path, out_dir: Path, target: str) -> Path:
    lo = shutil.which("libreoffice") or shutil.which("soffice")
    if not lo:
        raise RuntimeError("未找到 LibreOffice（libreoffice/soffice），无法导出 Word/PDF")
    out_dir.mkdir(parents=True, exist_ok=True)
    # Explicit filters are more reliable than bare "docx" on some LO builds.
    convert_to = {
        "docx": 'docx:MS Word 2007 XML',
        "pdf": "pdf:writer_pdf_Export",
        "odt": "odt:writer8",
    }.get(target, target)
    ext = target.split(":")[0]
    expected = out_dir / f"{source.stem}.{ext}"
    if expected.exists():
        expected.unlink()
    cmd = [
        lo,
        "--headless",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to",
        convert_to,
        "--outdir",
        str(out_dir),
        str(source.resolve()),
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=180)
    if result.returncode != 0 or not expected.is_file():
        detail = (result.stderr or result.stdout or "").strip()[:400]
        raise RuntimeError(f"LibreOffice 转为 {target} 失败: {detail or 'unknown error'}")
    return expected


def markdown_to_docx(
    markdown: str,
    output_path: Path | str,
    *,
    title: str = "",
    search_roots: list[Path] | None = None,
) -> Path:
    """Convert Markdown → HTML → .docx via LibreOffice."""

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    html_path = out.with_suffix(".html")
    html_path.write_text(
        markdown_to_html(markdown, title=title, search_roots=search_roots),
        encoding="utf-8",
    )
    produced = _libreoffice_convert(html_path, out.parent, "docx")
    if produced.resolve() != out.resolve():
        shutil.move(str(produced), str(out))
    return out


def docx_to_pdf(docx_path: Path | str, output_dir: Path | str | None = None) -> Path:
    source = Path(docx_path).resolve()
    out_dir = Path(output_dir or source.parent).resolve()
    return _libreoffice_convert(source, out_dir, "pdf")


def html_to_pdf(html_path: Path | str, output_dir: Path | str | None = None) -> Path:
    source = Path(html_path).resolve()
    out_dir = Path(output_dir or source.parent).resolve()
    return _libreoffice_convert(source, out_dir, "pdf")


def export_word_and_pdf(
    markdown: str,
    output_dir: Path | str,
    *,
    basename: str = "manuscript_final",
    title: str = "",
    search_roots: list[Path] | None = None,
) -> dict[str, str]:
    """Write Markdown + HTML + Word + PDF."""

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / f"{basename}.md"
    md_path.write_text(markdown, encoding="utf-8")
    html_path = out / f"{basename}.html"
    html_path.write_text(
        markdown_to_html(markdown, title=title, search_roots=search_roots),
        encoding="utf-8",
    )

    result = {
        "markdown": str(md_path),
        "html": str(html_path),
        "docx": "",
        "pdf": "",
        "docx_error": "",
        "pdf_error": "",
    }

    try:
        produced = _libreoffice_convert(html_path, out, "docx")
        docx_path = out / f"{basename}.docx"
        if produced.resolve() != docx_path.resolve():
            shutil.move(str(produced), str(docx_path))
        result["docx"] = str(docx_path)
    except Exception as error:  # noqa: BLE001
        # Fallback: HTML → ODT → DOCX
        try:
            odt = _libreoffice_convert(html_path, out, "odt")
            produced = _libreoffice_convert(odt, out, "docx")
            docx_path = out / f"{basename}.docx"
            if produced.resolve() != docx_path.resolve():
                shutil.move(str(produced), str(docx_path))
            result["docx"] = str(docx_path)
        except Exception as nested:  # noqa: BLE001
            result["docx_error"] = f"{error}; odt-fallback: {nested}"[:400]

    try:
        # Prefer PDF from HTML (better Chinese/layout fidelity than docx roundtrip)
        produced = _libreoffice_convert(html_path, out, "pdf")
        pdf_path = out / f"{basename}.pdf"
        if produced.resolve() != pdf_path.resolve():
            shutil.move(str(produced), str(pdf_path))
        result["pdf"] = str(pdf_path)
    except Exception as error:  # noqa: BLE001
        # Fallback: docx → pdf
        if result["docx"]:
            try:
                pdf_path = docx_to_pdf(result["docx"], out)
                target = out / f"{basename}.pdf"
                if pdf_path.resolve() != target.resolve():
                    shutil.move(str(pdf_path), str(target))
                    pdf_path = target
                result["pdf"] = str(pdf_path)
            except Exception as nested:  # noqa: BLE001
                result["pdf_error"] = f"{error}; fallback: {nested}"[:400]
        else:
            result["pdf_error"] = str(error)[:400]
    return result
