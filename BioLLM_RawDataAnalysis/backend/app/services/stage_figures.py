"""Publish completed plotting process outputs independently of final REPORT."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from ..models import utc_now
from .artifacts import ArtifactService, SAFE_IDENTIFIER


PLOT_PROCESSES = {
    "TAXONOMY_PLOTS": ("taxonomy_plots", "figure.taxonomy"),
    "FUNCTIONAL_PLOTS": ("functional_plots", "figure.functional"),
    "MAG_PLOTS": ("mag_plots", "figure.mag"),
}


def _regular(path: Path, root: Path) -> bool:
    """Reject links in every component, not just the leaf."""
    return path.is_file() and path.resolve() == path and path.is_relative_to(root)


def _generated(value):
    if isinstance(value, dict):
        if value.get("status") == "generated":
            outputs = value.get("outputs", [])
            if isinstance(outputs, list):
                yield from (name for name in outputs if isinstance(name, str))
            if isinstance(value.get("output"), str):
                yield value["output"]
        for child in value.values():
            yield from _generated(child)


def publish_stage_figures(service: ArtifactService, task_id: str) -> None:
    if not SAFE_IDENTIFIER.fullmatch(task_id):
        return
    state = service.output_root.parent
    output = service.output_root / task_id
    work = state / "work" / task_id
    trace = output / "trace.tsv"
    if not _regular(trace, service.output_root) or work.resolve() != work:
        return
    # Serialize with task deletion/cache cleanup and concurrent catalogue requests.
    with service.repository._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone() is None:
            return
        try:
            with trace.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
        except (OSError, UnicodeError):
            return
        for row in rows:
            process = row.get("name", "").split(" (")[0].split(":")[-1]
            if process not in PLOT_PROCESSES or row.get("status") not in {"COMPLETED", "CACHED"}:
                continue
            if row.get("exit") not in {"0", "-", ""}:
                continue
            task_hash = row.get("hash", "")
            if not re.fullmatch(r"[0-9a-f]{2}/[0-9a-f]{6,30}", task_hash):
                continue
            candidates = list(work.glob(task_hash + "*"))
            if len(candidates) != 1 or candidates[0].resolve() != candidates[0]:
                continue
            folder, artifact_type = PLOT_PROCESSES[process]
            process_dir = candidates[0]
            plots = process_dir / folder
            provenance = plots / f"{folder}.provenance.json"
            try:
                exit_file = process_dir / ".exitcode"
                if not _regular(exit_file, work) or exit_file.read_text().strip() != "0":
                    continue
                if not _regular(provenance, work) or provenance.stat().st_size > 2_000_000:
                    continue
                payload = json.loads(provenance.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or payload.get("status") != "completed" or payload.get("analysis") != folder:
                    continue
                for name in sorted(set(_generated(payload.get("plots", {})))):
                    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.png", name):
                        continue
                    source = plots / name
                    if not _regular(source, work) or source.stat().st_size > 50_000_000:
                        continue
                    content = source.read_bytes()
                    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                        continue
                    digest = hashlib.sha256(content).hexdigest()
                    artifact_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{task_id}/{folder}/{name}/{digest}"))
                    if connection.execute("SELECT artifact_id FROM artifacts WHERE artifact_id=?", (artifact_id,)).fetchone():
                        continue
                    destination = output / "stage_figures" / folder / digest / name
                    if destination.resolve() != destination:
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
                        temporary = Path(handle.name)
                        handle.write(content)
                    try:
                        os.replace(temporary, destination)
                    finally:
                        temporary.unlink(missing_ok=True)
                    metadata = {
                        "analysis_version": payload.get("analysis_version", "1.0"),
                        "stage_result": True, "source": folder,
                        "parameters": {"top_n": payload.get("top_n")},
                        "paper_sections": ["Result"], "trace_hash": task_hash,
                        "generated_at": payload.get("created_at"),
                        "attribution_boundary": payload.get("attribution_boundary"),
                    }
                    record = dict(
                        artifact_id=artifact_id, task_id=task_id, node_id=folder,
                        producer=folder, artifact_type=artifact_type, schema_version="1.0",
                        sample_scope="cohort", sample_id=None, cohort_id=task_id,
                        media_type="image/png", path=str(destination), sha256=digest,
                        size_bytes=len(content), metadata_json=json.dumps(metadata, ensure_ascii=False),
                        downloadable=1, status="active", created_at=utc_now(),
                    )
                    connection.execute(
                        f"INSERT INTO artifacts ({', '.join(record)}) VALUES ({', '.join('?' for _ in record)})",
                        tuple(record.values()),
                    )
            except (OSError, ValueError, TypeError):
                # Incomplete/invalid files are never served as successful figures.
                continue


def figure_notices(service: ArtifactService, task_id: str) -> list[dict]:
    """Read only plotting provenance selected by this task's trace, never arbitrary paths."""
    if not SAFE_IDENTIFIER.fullmatch(task_id):
        return []
    output = service.output_root / task_id
    work = service.output_root.parent / 'work' / task_id
    trace = output / 'trace.tsv'
    if not _regular(trace, service.output_root) or work.resolve() != work:
        return []
    notices = []
    translations = {
        'Bray-Curtis PCoA requires at least two samples with positive abundance totals': '至少需要两个具有非零丰度的样本才能生成 Bray–Curtis PCoA；当前条件不足，已跳过。',
        'no positive named functions; diagnostic categories excluded': '排除未注释诊断类别后，没有非零的已命名功能，已跳过该图；这不等同于生物学功能不存在。',
        'Coverage is not additive abundance; no composition plot': '覆盖度不是可相加的丰度，不生成组成比例图。',
    }

    def walk(value, keys):
        if not isinstance(value, dict):
            return
        if value.get('status') in {'skipped','failed'}:
            yield keys, value.get('reason', '绘图未完成')
        for key, child in value.items():
            if isinstance(child, dict):
                yield from walk(child, keys + [key])

    try:
        with trace.open(encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle, delimiter='\t'))
        for row in rows:
            process = row.get('name','').split(' (')[0].split(':')[-1]
            if process not in PLOT_PROCESSES or row.get('status') not in {'COMPLETED','CACHED','FAILED'}:
                continue
            task_hash = row.get('hash','')
            if not re.fullmatch(r'[0-9a-f]{2}/[0-9a-f]{6,30}',task_hash):
                continue
            candidates = list(work.glob(task_hash + '*'))
            if len(candidates) != 1:
                continue
            folder, kind = PLOT_PROCESSES[process]
            source = candidates[0] / folder / f'{folder}.provenance.json'
            revised = output / 'figures_v2' / folder / f'{folder}.provenance.json'
            if _regular(revised, output):
                source = revised
            if source.resolve() != source or not source.is_file() or source.stat().st_size > 2_000_000:
                continue
            payload = json.loads(source.read_text(encoding='utf-8'))
            if not isinstance(payload, dict) or payload.get('analysis') != folder:
                continue
            from .redaction import redact_log
            for keys, reason in walk(payload, []):
                key = '.'.join(keys) or folder
                reason = str(reason)
                notices.append(dict(id=f'{folder}:{key}', category=kind.split('.')[1], artifact_type=kind,
                                    node_id=folder, sample_id=None,
                                    state='generation_failed' if payload.get('status') == 'failed' else 'skipped',
                                    message=f'{key}：{translations.get(reason, redact_log(reason))}'))
    except (OSError, ValueError, TypeError):
        pass
    return notices
