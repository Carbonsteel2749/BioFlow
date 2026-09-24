from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..models import TaskRepository
from .result_units import measurement, display_value


PREVIEW_ROWS = 20
MAX_SCANNED_ROWS = 10_000
MAX_PREVIEW_FILE_BYTES = 64 * 1024 * 1024
MULTIQC_CSP = (
    "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; font-src 'self' data:; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'self'"
)


class PreviewService:
    def __init__(self, settings: Settings, repository: TaskRepository):
        self.settings = settings
        self.repository = repository

    def preview(self, task_id: str, sample_id: str | None = None) -> dict[str, Any]:
        task = self._require_task(task_id)
        try:
            with Path(task['manifest_path']).open(encoding='utf-8-sig', newline='') as handle:
                content = handle.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise ValueError('样本清单超过预览读取上限')
            samples = list(dict.fromkeys(r['sample_id'].strip() for r in csv.DictReader(content.splitlines())))
            if not samples or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', s) for s in samples):
                raise ValueError('样本清单中的样本 ID 不完整')
        except (OSError, UnicodeError, KeyError, csv.Error) as exc:
            raise ValueError('无法核实任务样本清单，拒绝混合展示结果') from exc
        selected = samples[0] if sample_id is None else sample_id
        if selected not in samples:
            raise ValueError('所选样本不属于此任务')
        root = self._output_root(task_id)
        multiqc = _first_safe_file(
            root, lambda path: path.name.lower() == "multiqc_report.html"
        )
        taxonomy = _sample_table(root, ('species_abundance.tsv',), selected, len(samples) == 1)
        ko = _sample_table(root, ('read_ko.tsv',), selected, len(samples) == 1)
        ec = _sample_table(root, ('read_ec.tsv',), selected, len(samples) == 1)
        pathways = _sample_table(root, ('read_pathabundance.tsv', 'read_pathways.tsv'), selected, len(samples) == 1)
        qc_summary = [row for row in _qc_summary(root) if row['sample_id'] == selected]
        taxonomy_top = _taxonomy_top(taxonomy, selected) if taxonomy else []
        if not _safe_files(root, lambda p: p.suffix in {'.tsv', '.json', '.html'} or '.tsv.' in p.name):
            raise FileNotFoundError("result preview is not available")
        return {
            "sample_ids": samples,
            "selected_sample_id": selected,
            "multiqc_url": (
                f"/api/tasks/{task_id}/reports/multiqc" if multiqc else None
            ),
            "qc_summary": qc_summary,
            "taxonomy_top": taxonomy_top,
            "taxonomy_measurement": measurement(taxonomy['records'] if taxonomy else [], 'taxonomy'),
            "taxonomy_truncated": bool(taxonomy and taxonomy.get('truncated')),
            "ko": _table_preview(ko) if ko else None,
            "ec": _table_preview(ec) if ec else None,
            "pathways": _table_preview(pathways) if pathways else None,
        }

    def multiqc_report(self, task_id: str) -> Path:
        self._require_task(task_id)
        report = _first_safe_file(
            self._output_root(task_id),
            lambda path: path.name.lower() == "multiqc_report.html",
        )
        if report is None:
            raise FileNotFoundError("MultiQC report is not available")
        return report

    def _require_task(self, task_id: str) -> dict:
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    def _output_root(self, task_id: str) -> Path:
        return self.settings.state_root / "outputs" / task_id


def _safe_files(root: Path, predicate: Callable[[Path], bool]) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        return []
    approved = root.resolve(strict=True)
    matches: list[Path] = []
    for candidate in root.rglob("*"):
        if candidate.is_symlink() or not predicate(candidate):
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(approved)
        except (FileNotFoundError, OSError, ValueError):
            continue
        if (
            resolved.is_file()
            and resolved.stat().st_size <= MAX_PREVIEW_FILE_BYTES
        ):
            matches.append(resolved)
    return sorted(set(matches), key=lambda path: (len(path.parts), str(path)))


def _first_safe_file(
    root: Path, predicate: Callable[[Path], bool]
) -> Path | None:
    matches = _safe_files(root, predicate)
    return matches[0] if matches else None


def _sample_table(root: Path, names: tuple[str, ...], sample: str, single: bool) -> dict | None:
    patterns = [re.compile(rf'^{re.escape(Path(n).stem)}(?:\.\d+)?{re.escape(Path(n).suffix)}(?:\.\d+)?$', re.I) for n in names]
    paths = _safe_files(root, lambda p: any(pattern.fullmatch(p.name) for pattern in patterns))
    resolved_root = root.resolve()

    def scope(path):
        parts = path.relative_to(resolved_root).parts
        canonical = len(parts) >= 3 and parts[0] in {'taxonomy', 'functional_annotation'}
        # Packaged folder names are normalized and may collide. Only original
        # canonical directories establish ownership; packaged rows need labels.
        owner = parts[1] if canonical else None
        return canonical, owner

    # Prefer the original sample output; do not double count report copies.
    paths.sort(key=lambda p: (0 if scope(p) == (True, sample) else 1 if scope(p)[1] == sample else 2,
                              next(i for i, pattern in enumerate(patterns) if pattern.fullmatch(p.name)), len(p.parts), str(p)))
    truncated_candidate = None
    for path in paths:
        _, owner = scope(path)
        if owner is not None and owner != sample:
            continue
        try:
            with path.open(encoding='utf-8-sig', newline='') as handle:
                reader = csv.DictReader(handle, delimiter='\t')
                columns = reader.fieldnames or []
                labelled = 'sample_id' in columns
                if not columns or (not labelled and not (owner == sample or single)):
                    continue
                rows, truncated = [], False
                for index, row in enumerate(reader):
                    if index >= MAX_SCANNED_ROWS:
                        truncated = True
                        break
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError('incomplete result row')
                    if labelled and row['sample_id'].strip() != sample:
                        continue
                    rows.append(row)
                result = {'columns': columns, 'records': rows, 'truncated': truncated}
                if not truncated:
                    result['total_rows'] = len(rows)
                if rows or owner == sample:
                    return result
                if truncated:
                    truncated_candidate = result
        except (OSError, UnicodeError, csv.Error, ValueError):
            # A broken selected-sample source must not expose another sample.
            if owner == sample:
                return None
    return truncated_candidate


def _coerce(value: str) -> str | int | float | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        number = float(stripped)
    except ValueError:
        return stripped
    return int(number) if number.is_integer() else number


def _table_preview(data: dict) -> dict[str, Any]:
    columns = data['columns']
    result = {'columns': columns, 'rows': [
        [row[col] if col == 'sample_id' else _coerce(row[col]) for col in columns]
        for row in data['records'][:PREVIEW_ROWS]], 'truncated': data['truncated'],
        'measurement': measurement(data['records'], 'functional')}
    if 'total_rows' in data:
        result['total_rows'] = data['total_rows']
    return result


def _taxonomy_top(data: dict, sample: str) -> list[dict[str, str | float]]:
    values: list[dict[str, str | float]] = []
    info = measurement(data['records'], 'taxonomy')
    if info['status'] == 'conflict':
        return []
    for row in data['records']:
            name = next(
                (row.get(key, "").strip() for key in ("taxonomy", "name", "species") if row.get(key, "").strip()),
                "",
            )
            raw_abundance = row.get("abundance") or row.get("relative_abundance")
            if not name or raw_abundance is None:
                continue
            try:
                abundance = display_value(raw_abundance, info)
            except ValueError:
                continue
            if not math.isfinite(abundance) or abundance < 0:
                continue
            values.append({"sample_id": sample, "name": name, "abundance": round(abundance, 6)})
    values.sort(key=lambda row: float(row["abundance"]), reverse=True)
    return values[:PREVIEW_ROWS]


def _host_metrics(root: Path) -> dict[str, dict]:
    result = {}
    for path in _safe_files(root, lambda p: p.name.endswith('.host_depletion.metrics.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            sample = payload['sample_id']
            incoming, retained, removed = (payload[k] for k in ('input_pair_count', 'retained_pair_count', 'removed_pair_count'))
            if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in (incoming, retained, removed)):
                continue
            if retained + removed != incoming:
                continue
            result[sample] = {**payload, 'removed_pct': 100 * removed / incoming if incoming else None}
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result


def _qc_summary(root: Path) -> list[dict[str, Any]]:
    host = _host_metrics(root)
    rows: list[dict[str, str | int | float]] = []
    fastp_files = _safe_files(
        root, lambda path: path.name.lower().endswith(".fastp.json")
    )
    for path in fastp_files[:1000]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            summary = payload["summary"]
            raw_reads = int(summary["before_filtering"]["total_reads"])
            clean_reads = int(summary["after_filtering"]["total_reads"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
            continue
        sample_id = path.name.split(".fastp.json", 1)[0]
        rows.append(
            {
                "sample_id": sample_id,
                "raw_reads": raw_reads,
                "clean_reads": clean_reads,
                "host_removed_pct": host.get(sample_id, {}).get('removed_pct'),
            }
        )
    return rows
