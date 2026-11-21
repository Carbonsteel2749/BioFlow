"""Store one LaTeX template per project and fill it with a finished manuscript."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from io import BytesIO
from pathlib import Path

from frontend.latex_preview import (
    _document_title,
    _escape,
    manuscript_opening,
    manuscript_sections,
    section_bodies,
    session_to_latex,
)
from frontend.session_store import WritingSession

_SLOT = re.compile(r"<<\s*([A-Za-z_]+)\s*>>")
_UNSAFE = re.compile(
    r"\\write18|\\ShellEscape|\\immediate\s*\\write|\\openout"
    r"|\\(?:input|include)\s*\{[^}]*\.\."
    r"|\\(?:input|include)\s*\{[/\\]",
    re.IGNORECASE,
)
_ALLOWED = {".tex", ".cls", ".sty", ".bst", ".bib", ".fd", ".def", ".clo", ".cfg", ".png", ".jpg", ".jpeg", ".pdf"}
_SLOT_TO_SECTION = {
    "ABSTRACT": "abstract",
    "INTRODUCTION": "introduction",
    "METHODS": "methods",
    "RESULTS": "results",
    "DISCUSSION": "discussion",
    "CONCLUSION": "conclusion",
    "BACK_MATTER": "back_matter",
    "BACKMATTER": "back_matter",
}
_KNOWN_SLOTS = set(_SLOT_TO_SECTION) | {"TITLE", "QUESTION"}
_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")


class TemplateError(ValueError):
    pass


def template_root() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime" / "latex-templates"


def project_dir(project_id: str) -> Path:
    if not _PROJECT_ID.fullmatch(project_id or ""):
        raise TemplateError("无效的项目编号")
    root = template_root().resolve()
    path = (root / project_id).resolve()
    if path.parent != root:
        raise TemplateError("无效的项目编号")
    return path


def _unsafe(text: str) -> str:
    if _UNSAFE.search(text or ""):
        return "模板包含不允许的命令，请去掉 \\write18、\\openout 和越界 \\input。"
    return ""


def _safe_relative(name: str) -> Path | None:
    pure = Path(name)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        return None
    if pure.suffix.lower() not in _ALLOWED:
        return None
    return pure


def _choose_main(files: dict[Path, bytes]) -> Path:
    tex_files = [path for path in files if path.suffix.lower() == ".tex"]
    if not tex_files:
        raise TemplateError("请上传 .tex 文件，或上传包含主 tex 的 zip。")
    documents = []
    for path in tex_files:
        text = files[path].decode("utf-8", errors="replace")
        if r"\documentclass" in text or _SLOT.search(text):
            documents.append(path)
    if not documents:
        raise TemplateError("模板需要包含 \\documentclass，或使用 <<ABSTRACT>> 这类空位。")
    if len(documents) == 1:
        return documents[0]
    preferred = [path for path in documents if path.name.lower() in {"main.tex", "manuscript.tex"}]
    if len(preferred) == 1:
        return preferred[0]
    raise TemplateError("压缩包里有多个主 tex 文件，请只保留一个。")


def _check_main(text: str) -> str:
    problem = _unsafe(text)
    if problem:
        raise TemplateError(problem)
    slots = [name.upper() for name in _SLOT.findall(text) if name.upper() in _KNOWN_SLOTS]
    if slots:
        return "slots"
    if r"\documentclass" not in text or r"\begin{document}" not in text or r"\end{document}" not in text:
        raise TemplateError("模板需要包含 \\documentclass 和正文，或使用 <<TITLE>>、<<ABSTRACT>> 等空位。")
    return "preamble"


def _library_path(project_id: str) -> Path:
    return project_dir(project_id) / "library.json"


def _empty_library() -> dict:
    return {"active": "", "items": []}


def _load_library(project_id: str) -> dict:
    folder = project_dir(project_id)
    if not folder.exists():
        return _empty_library()
    _migrate_single_template(folder)
    path = _library_path(project_id)
    if not path.is_file():
        return _empty_library()
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("active", "")
    data.setdefault("items", [])
    return data


def _save_library(project_id: str, data: dict) -> None:
    folder = project_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    _library_path(project_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _migrate_single_template(folder: Path) -> None:
    legacy = folder / "template.json"
    if not legacy.is_file() or (folder / "items").exists():
        return
    meta = json.loads(legacy.read_text(encoding="utf-8"))
    item_id = re.sub(r"[^a-f0-9]", "", str(meta.get("stamp") or ""))[:16] or "legacy"
    dest = folder / "items" / item_id
    dest.mkdir(parents=True, exist_ok=True)
    for child in list(folder.iterdir()):
        if child.name == "items":
            continue
        shutil.move(str(child), dest / child.name)
    (folder / "library.json").write_text(
        json.dumps({"active": item_id, "items": [item_id]}, ensure_ascii=False),
        encoding="utf-8",
    )


def _item_dir(project_id: str, template_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{8,32}", template_id or ""):
        raise TemplateError("模板不在库中")
    folder = project_dir(project_id).resolve()
    path = (folder / "items" / template_id).resolve()
    if path.parent.parent != folder:
        raise TemplateError("模板不在库中")
    return path


def list_templates(project_id: str) -> dict:
    library = _load_library(project_id) if project_id else _empty_library()
    items = []
    for template_id in library.get("items") or []:
        meta_path = project_dir(project_id) / "items" / template_id / "template.json"
        if not meta_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        items.append({"id": template_id, "name": meta.get("name") or template_id, "mode": meta.get("mode") or "", "slots": meta.get("slots") or []})
    active = library.get("active") or ""
    if active and active not in {item["id"] for item in items}:
        active = ""
    return {"active": active, "items": items}


def select_template(project_id: str, template_id: str) -> dict:
    library = _load_library(project_id)
    choice = (template_id or "").strip()
    if choice and choice not in library.get("items", []):
        raise TemplateError("模板不在库中")
    library["active"] = choice
    _save_library(project_id, library)
    return list_templates(project_id)


def install_template(project_id: str, filename: str, payload: bytes) -> dict:
    if len(payload) > 8_000_000:
        raise TemplateError("模板文件超过 8 MB。")
    folder = project_dir(project_id)
    name = Path(filename or "template.tex").name
    suffix = Path(name).suffix.lower()
    files: dict[Path, bytes] = {}
    if suffix == ".zip":
        try:
            archive = zipfile.ZipFile(BytesIO(payload))
        except zipfile.BadZipFile as exc:
            raise TemplateError("压缩包无法读取。") from exc
        total = 0
        members = [info for info in archive.infolist() if not info.is_dir()]
        if len(members) > 40:
            raise TemplateError("压缩包内文件过多。")
        for info in members:
            relative = _safe_relative(info.filename)
            if relative is None:
                raise TemplateError("压缩包包含不允许的文件或路径。")
            if info.file_size > 8_000_000:
                raise TemplateError("压缩包内有超过 8 MB 的文件。")
            total += info.file_size
            if total > 20_000_000:
                raise TemplateError("模板解压后超过 20 MB。")
            files[relative] = archive.read(info)
    elif suffix == ".tex":
        files[Path(name)] = payload
    else:
        raise TemplateError("请上传 .tex 或 .zip。")
    for path, content in files.items():
        if path.suffix.lower() in {".tex", ".cls", ".sty", ".bst", ".bib", ".def", ".clo", ".cfg"}:
            problem = _unsafe(content.decode("utf-8", errors="replace"))
            if problem:
                raise TemplateError(problem)
    main = _choose_main(files)
    mode = _check_main(files[main].decode("utf-8", errors="replace"))
    item_id = hashlib.sha256(payload).hexdigest()[:16]
    folder = _item_dir(project_id, item_id)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        dest = folder / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    slots = []
    if mode == "slots":
        slots = list(dict.fromkeys(token.upper() for token in _SLOT.findall(files[main].decode("utf-8", errors="replace")) if token.upper() in _KNOWN_SLOTS))
    meta = {
        "installed": True,
        "id": item_id,
        "name": name,
        "main": main.as_posix(),
        "mode": mode,
        "slots": slots,
        "stamp": _stamp(folder),
    }
    (folder / "template.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    library = _load_library(project_id)
    if item_id not in library["items"]:
        library["items"].append(item_id)
    library["active"] = item_id
    _save_library(project_id, library)
    return meta


def _stamp(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in folder.rglob("*") if item.is_file() and item.name != "template.json"):
        digest.update(str(path.relative_to(folder)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def template_status(project_id: str) -> dict:
    empty = {"installed": False, "name": "", "mode": "", "slots": [], "stamp": "", "id": ""}
    if not project_id:
        return empty
    library = list_templates(project_id)
    active = library.get("active") or ""
    if not active:
        return empty
    meta_path = project_dir(project_id) / "items" / active / "template.json"
    if not meta_path.is_file():
        return empty
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    data["installed"] = True
    data["id"] = active
    return data


def remove_template(project_id: str, template_id: str) -> dict:
    folder = _item_dir(project_id, template_id)
    if folder.exists():
        shutil.rmtree(folder)
    library = _load_library(project_id)
    library["items"] = [item for item in library.get("items") or [] if item != template_id]
    if library.get("active") == template_id:
        library["active"] = ""
    _save_library(project_id, library)
    return list_templates(project_id)


def _values(session: WritingSession, language: str) -> dict[str, str]:
    choice = "en" if (language or "").strip().lower() == "en" else "zh"
    bodies = {key: body for key, _heading, body in section_bodies(session, choice)}
    question = (session.research_question or "").strip()
    if choice == "en" and len(re.findall(r"[\u4e00-\u9fff]", question)) >= 8:
        question = ""
    values = {
        "TITLE": _escape(_document_title(session, choice)),
        "QUESTION": _escape(question),
    }
    for slot, key in _SLOT_TO_SECTION.items():
        values[slot] = bodies.get(key, "")
    return values


def fill_template_text(template: str, session: WritingSession, language: str) -> str:
    slots = [name.upper() for name in _SLOT.findall(template) if name.upper() in _KNOWN_SLOTS]
    if slots:
        values = _values(session, language)

        def replace(match: re.Match[str]) -> str:
            name = match.group(1).upper()
            if name not in _KNOWN_SLOTS:
                return match.group(0)
            return values.get(name, "")

        return _SLOT.sub(replace, template)
    begin = template.find(r"\begin{document}")
    end = template.rfind(r"\end{document}")
    if begin < 0 or end < begin:
        return session_to_latex(session, language)
    preamble = template[:begin].rstrip()
    if "graphicx" not in preamble:
        preamble += "\n\\usepackage{graphicx}\n\\graphicspath{{figures/}}\n"
    elif "graphicspath" not in preamble:
        preamble += "\n\\graphicspath{{figures/}}\n"
    title = _escape(_document_title(session, language))
    if r"\title" not in preamble:
        preamble += f"\n\\title{{{title}}}\n"
    return (
        preamble
        + "\n\\begin{document}\n"
        + manuscript_opening(session, language)
        + "\n"
        + manuscript_sections(session, language)
        + "\n\\end{document}\n"
    )


def render_for_project(session: WritingSession, language: str, project_id: str) -> tuple[str, Path | None, str]:
    status = template_status(project_id) if project_id else {"installed": False}
    if not status.get("installed"):
        return session_to_latex(session, language), None, ""
    folder = project_dir(project_id) / "items" / status["id"]
    main = folder / status["main"]
    if not main.is_file():
        return session_to_latex(session, language), None, ""
    text = main.read_text(encoding="utf-8")
    return fill_template_text(text, session, language), folder, status.get("stamp") or ""
