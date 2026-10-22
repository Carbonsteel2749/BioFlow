"""Inventory the paper-facing files in a result archive.

Different analyses ship different filenames. Selection uses folder roles, not
assay-specific names: figures, final tables, provenance parameters, package
summaries, and written interpretation. Process files stay in the archive and
are left out of the catalog.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from article_writing.contracts import AnalysisBundle, FigureRef

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"}
TABLE_SUFFIXES = {".tsv", ".csv"}
TEXT_SUFFIXES = {".txt", ".md", ".json"}
SKIP_DIR_NAMES = {
    "multiqc_report_data",
    "logs",
    "__macosx",
    "intermediates",
    "execution",
    "raw",
    "qc",
}
SKIP_FILE_NAMES = {"llms-full.txt", "sha256sums", ".ds_store"}
# Top-level folders that hold manuscript inputs. Anything else in the archive
# is packaging or process output.
PAPER_ROOTS = {"figures", "tables", "provenance", "reports", "interpretation"}
CONCLUSION_MARKERS = ("conclusion", "finding", "interpretation", "narrative")
SUMMARY_MARKERS = ("summary", "readme")
_STATUS_ONLY_KEYS = {"reason", "status", "error", "message", "schema_version"}
_EMPTY_STATUS = {"", "none", "null", "empty", "skipped", "unavailable", "not_generated", "missing"}
MAX_ITEMS = 80
_FINDING_PREFIXES = ("主要发现", "局限", "key finding", "limitation", "finding:")
_HASH_RE = re.compile(r"^[0-9a-f]{16,}$", re.I)

_SLUG = re.compile(r"[^a-z0-9]+")


class CatalogItem:
    def __init__(
        self,
        *,
        item_id: str,
        kind: str,
        relative_path: str,
        path: str,
        label: str,
        excerpt: str,
    ) -> None:
        self.item_id = item_id
        self.kind = kind
        self.relative_path = relative_path
        self.path = path
        self.label = label
        self.excerpt = excerpt

    def to_dict(self) -> dict[str, str]:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "relative_path": self.relative_path,
            "path": self.path,
            "label": self.label,
            "excerpt": self.excerpt,
        }


def deliverable_archive(path: Path) -> Path | None:
    """Return the packaged result archive for a task directory or archive path."""

    if path.is_file() and _is_archive(path):
        return path
    if not path.is_dir():
        return None
    folder = path / "deliverables"
    if not folder.is_dir():
        return None
    archives = [item for item in folder.iterdir() if item.is_file() and _is_archive(item)]
    if not archives:
        return None
    task_name = path.name.lower()
    named = [item for item in archives if task_name and task_name in item.name.lower()]
    pool = named or archives
    return sorted(pool, key=lambda item: item.stat().st_mtime)[-1]


def build_catalog(root: Path) -> list[CatalogItem]:
    """List result files under ``root`` without assuming a particular assay."""

    root = root.resolve()
    found: list[CatalogItem] = []
    used_ids: set[str] = set()
    if not root.is_dir():
        return found
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if len(found) >= MAX_ITEMS:
            break
        relative = path.relative_to(root)
        if _skipped(relative):
            continue
        kind = _kind_for(relative)
        if not kind or not _for_paper(relative, path, kind):
            continue
        item_id = _item_id(relative, used_ids)
        excerpt = _excerpt(path, kind)
        found.append(
            CatalogItem(
                item_id=item_id,
                kind=kind,
                relative_path=relative.as_posix(),
                path=str(path),
                label=relative.stem.replace("_", " "),
                excerpt=excerpt,
            )
        )
    return prefer_png_figures(found)


def prefer_png_figures(items: list) -> list:
    """Keep one file per plot. When a PNG sits beside another format, keep the PNG."""

    png_keys: set[tuple[str, str]] = set()
    for item in items:
        identity = _figure_identity(item)
        if identity and identity[2] == ".png":
            png_keys.add((identity[0], identity[1]))
    kept = []
    for item in items:
        identity = _figure_identity(item)
        if identity and identity[2] != ".png" and (identity[0], identity[1]) in png_keys:
            continue
        kept.append(item)
    return kept


def _figure_identity(item: object) -> tuple[str, str, str] | None:
    if isinstance(item, CatalogItem):
        kind = item.kind
        relative = item.relative_path
    elif isinstance(item, dict):
        kind = str(item.get("kind") or "")
        relative = str(item.get("relative_path") or "")
    else:
        return None
    if kind != "figure" or not relative:
        return None
    path = Path(relative)
    return (path.parent.as_posix().lower(), path.stem.lower(), path.suffix.lower())


def catalog_from_raw(raw: dict | None) -> list[dict[str, str]]:
    if not isinstance(raw, dict):
        return []
    items = raw.get("archive_catalog")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("item_id")]


def attach_catalog(bundle: AnalysisBundle, items: list[CatalogItem]) -> AnalysisBundle:
    """Keep packaged displays on the bundle and store the full inventory."""

    raw = dict(bundle.raw or {})
    raw["archive_catalog"] = [item.to_dict() for item in items]
    refs = list(bundle.figure_refs)
    known_paths = {ref.path for ref in refs}
    known_ids = {ref.figure_id for ref in refs}
    for item in items:
        if item.kind not in {"figure", "table"} or item.path in known_paths:
            continue
        figure_id = item.item_id if item.item_id not in known_ids else f"fig_{item.item_id}"
        known_ids.add(figure_id)
        refs.append(
            FigureRef(
                figure_id=figure_id,
                path=item.path,
                caption=item.label,
                kind="table" if item.kind == "table" else "figure",
            )
        )
    return bundle.model_copy(update={"figure_refs": refs, "raw": raw})


def bundle_from_catalog(items: list[CatalogItem]) -> AnalysisBundle:
    """Build a bundle when the archive is not a known metagenomics layout."""

    summaries = [item.excerpt for item in items if item.kind == "summary" and item.excerpt]
    conclusions = [item.excerpt for item in items if item.kind == "conclusion" and item.excerpt]
    summary = " ".join(summaries)[:2000]
    if not summary:
        summary = f"The result archive contains {len(items)} catalogued files."
    return AnalysisBundle(
        summary=summary,
        key_findings=conclusions[:12],
        methods={},
        limitations=[
            "Only files present in the result archive were catalogued. "
            "Steps and conclusions absent from the archive were not added."
        ],
        raw={"source": "result_archive"},
    )


def render_catalog_for_prompt(items: list[dict[str, str]]) -> str:
    """One complete line per file. Lines are not cut off mid-sentence."""

    lines: list[str] = []
    for item in items:
        excerpt = " ".join(str(item.get("excerpt") or "").split())
        lines.append(
            f"- `{item.get('item_id')}` [{item.get('kind')}] "
            f"{item.get('relative_path')}: {excerpt or item.get('label') or ''}"
        )
    return "\n".join(lines)


def _is_archive(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".tar.gz") or name.endswith(".tgz") or name.endswith(".tar")


def _for_paper(relative: Path, path: Path, kind: str) -> bool:
    """Keep one file when its folder role is something a section can cite."""

    parts = [part.lower() for part in relative.parts]
    if len(parts) < 2 or parts[0] not in PAPER_ROOTS:
        return False
    if "by_sample" in parts or "checksum" in relative.name.lower():
        return False
    if parts[0] == "figures":
        return kind in {"figure", "parameter", "conclusion", "summary"}
    if parts[0] == "tables":
        return kind in {"table", "parameter", "summary", "conclusion"}
    if parts[0] == "provenance":
        return kind in {"parameter", "table", "summary"}
    if parts[0] == "reports":
        return kind in {"summary", "conclusion"}
    if _status_only_interpretation(path):
        return False
    return kind in {"conclusion", "summary", "parameter"}


def _status_only_interpretation(path: Path) -> bool:
    """Skip an interpretation file that only records that no text was written."""

    if path.suffix.lower() != ".json":
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or not payload:
        return False
    if not set(payload).issubset(_STATUS_ONLY_KEYS):
        return False
    status = str(payload.get("status") or "").strip().lower()
    return status in _EMPTY_STATUS


def _skipped(relative: Path) -> bool:
    parts = {part.lower() for part in relative.parts}
    if parts & SKIP_DIR_NAMES:
        return True
    if relative.name.lower() in SKIP_FILE_NAMES:
        return True
    suffix = relative.suffix.lower()
    if "qc" in parts and suffix in {".html", ".zip"}:
        return True
    return False


def _kind_for(relative: Path) -> str:
    suffix = relative.suffix.lower()
    stem = relative.stem.lower()
    blob = "/".join(part.lower() for part in relative.parts)
    if suffix in IMAGE_SUFFIXES:
        return "figure"
    if suffix in TABLE_SUFFIXES:
        return "table"
    if suffix not in TEXT_SUFFIXES:
        return ""
    if any(marker in blob for marker in CONCLUSION_MARKERS):
        return "conclusion"
    if any(marker in stem for marker in SUMMARY_MARKERS):
        return "summary"
    if "provenance" in blob or stem in {"parameters", "software_versions", "database_versions"}:
        return "parameter"
    if suffix == ".json" and path_is_small_enough(relative):
        return "parameter"
    return ""


def path_is_small_enough(relative: Path) -> bool:
    del relative
    return True


def _item_id(relative: Path, used: set[str]) -> str:
    slug = _SLUG.sub("_", relative.as_posix().lower()).strip("_")
    slug = slug[:60] or "item"
    candidate = f"cat_{slug}"
    number = 2
    while candidate in used:
        candidate = f"cat_{slug}_{number}"
        number += 1
    used.add(candidate)
    return candidate


def _excerpt(path: Path, kind: str) -> str:
    if kind == "figure":
        return path.name
    if kind == "table":
        return _table_excerpt(path)
    if path.suffix.lower() == ".json":
        return _json_excerpt(path, kind)
    return _text_excerpt(path, kind)


def _table_excerpt(path: Path) -> str:
    rows = _tsv_rows(path)
    if not rows:
        return path.name
    header = rows[0]
    if _is_version_table(header):
        return _version_rows(header, rows[1:])
    names = [cell for cell in header if cell]
    if len(names) > 12:
        shown = ", ".join(names[:12])
        return f"columns: {shown}; plus {len(names) - 12} more columns"
    return "columns: " + ", ".join(names) if names else path.name


def _is_version_table(header: list[str]) -> bool:
    cells = {cell.strip().lower() for cell in header}
    has_version = bool(cells & {"version", "release"})
    has_name = bool(cells & {"tool", "software", "name", "database_name", "section"})
    return has_version and has_name


def _version_rows(header: list[str], rows: list[list[str]]) -> str:
    keep = [
        index
        for index, name in enumerate(header)
        if name and "sha" not in name.lower() and "checksum" not in name.lower() and "hash" not in name.lower()
    ]
    parts: list[str] = []
    for row in rows:
        cells = [row[index].strip() for index in keep if index < len(row) and row[index].strip()]
        cells = [cell for cell in cells if not _HASH_RE.match(cell)]
        if cells:
            parts.append(" ".join(cells))
        if len(parts) >= 20:
            break
    return "; ".join(parts) or "columns: " + ", ".join(header)


def _json_excerpt(path: Path, kind: str) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    if kind == "conclusion":
        return _conclusion_from_json(payload) or _scalar_pairs(payload)
    if isinstance(payload, dict) and kind == "summary":
        counts = payload.get("counts")
        merged = dict(payload)
        if isinstance(counts, dict):
            merged.update(counts)
        return _scalar_pairs(merged)
    return _scalar_pairs(payload)


def _conclusion_from_json(payload: object) -> str:
    chunks: list[str] = []
    if isinstance(payload, dict):
        limitations = payload.get("limitations")
        if isinstance(limitations, list):
            chunks.extend(str(item).strip() for item in limitations if str(item).strip())
        text = payload.get("text")
        if isinstance(text, str):
            chunks.extend(_finding_lines(text))
    return _join_unique(chunks)


def _text_excerpt(path: Path, kind: str) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if kind == "conclusion":
        findings = _finding_lines(text)
        if findings:
            return _join_unique(findings)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    useful = [line for line in lines if "sha256" not in line.lower() and not line.lower().startswith("verify ")]
    return " | ".join(useful[:12])


def _finding_lines(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if any(lowered.startswith(prefix) for prefix in _FINDING_PREFIXES):
            found.append(stripped)
    return found


def _scalar_pairs(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            continue
        rendered = str(value).strip()
        key_name = str(key).lower()
        if not rendered or len(rendered) > 80:
            continue
        if any(token in key_name for token in ("sha", "checksum", "hash")):
            continue
        if _HASH_RE.match(rendered) or rendered.startswith("[HOME]") or "/" in rendered:
            continue
        parts.append(f"{key}={rendered}")
        if len(parts) >= 24:
            break
    if parts:
        return "; ".join(parts)
    names = [str(key) for key in list(payload)[:12]]
    return "keys: " + ", ".join(names) if names else ""


def _join_unique(chunks: list[str]) -> str:
    seen: set[str] = set()
    kept: list[str] = []
    for chunk in chunks:
        cleaned = " ".join(chunk.split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        kept.append(cleaned)
    return " | ".join(kept)


def _tsv_rows(path: Path) -> list[list[str]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows: list[list[str]] = []
    for line in lines:
        if not line.strip():
            continue
        rows.append([cell.strip() for cell in line.split("\t")])
        if len(rows) > 40:
            break
    return rows
