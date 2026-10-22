"""Publish completed plotting process outputs independently of final REPORT."""
from __future__ import annotations

import csv
import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from ..models import utc_now
from .artifacts import ArtifactService, SAFE_IDENTIFIER


PLOT_PROCESSES = {
    "READS_PLOTS": ("reads_plots", "figure.qc"),
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


def _safe_svg(content: bytes) -> bool:
    # Keep only the XML declaration, never stylesheet/other processing instructions.
    if b'<?' in re.sub(rb'<\?xml\s+[^?]*\?>', b'', content):
        return False
    if b'<!ENTITY' in content.upper():
        return False
    # Matplotlib emits this standard external declaration; ElementTree does not
    # fetch it. No internal subset or other DTD is accepted.
    allowed = rb'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"\s+"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
    if b'<!DOCTYPE' in re.sub(allowed, b'', content).upper():
        return False
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return False
    if root.tag.split('}')[-1] != 'svg':
        return False
    def safe_style(value: str) -> bool:
        if '@import' in value.lower() or '\\' in value:
            return False
        return all(target.strip().strip('\"\'').startswith('#')
            for target in re.findall(r'url\s*\((.*?)\)', value, flags=re.I | re.S))
    for element in root.iter():
        tag = element.tag.split('}')[-1].lower()
        if tag in {'script', 'foreignobject', 'iframe', 'set', 'animate', 'animatemotion', 'animatetransform'}:
            return False
        if tag == 'style' and not safe_style(element.text or ''):
            return False
        for key, value in element.attrib.items():
            key = key.split('}')[-1].lower()
            if key.startswith('on') or key == 'base':
                return False
            if key in {'href', 'src'} and not value.startswith('#'):
                prefix = 'data:image/png;base64,'
                if tag != 'image' or not value.startswith(prefix):
                    return False
                try:
                    decoded = base64.b64decode(value[len(prefix):].strip(), validate=True)
                except (ValueError, binascii.Error):
                    return False
                if not decoded.startswith(b'\x89PNG\r\n\x1a\n'):
                    return False
            if not safe_style(value):
                return False
    return True


def _refresh_figure_context(connection, identifier, payload, **extra):
    row = connection.execute('SELECT metadata_json FROM artifacts WHERE artifact_id=?', (identifier,)).fetchone()
    metadata = json.loads(row[0])
    for key in ('analysis_version', 'plot_module_version', 'figure_skill', 'figure_plan', 'display_scale',
                'units', 'database_context', 'database_profile', 'database_release', 'missingness_policy',
                'diversity', 'ordination', 'attribution_boundary'):
        if key in payload:
            metadata[key] = payload[key]
    metadata.update(generated_at=payload.get('created_at'), **extra)
    connection.execute('UPDATE artifacts SET metadata_json=? WHERE artifact_id=?',
        (json.dumps(metadata, ensure_ascii=False), identifier))


def _publish_companions(connection, *, task_id, folder, kind, plots, work, output, name, payload, png_id):
    stem = Path(name).stem
    candidates = [(stem + suffix, media, kind if suffix in {'.svg', '.pdf'} else 'figure.data')
        for suffix, media in [('.svg', 'image/svg+xml'), ('.pdf', 'application/pdf'),
            ('.data.tsv', 'text/tab-separated-values'), ('.source.tsv', 'text/tab-separated-values'),
            ('.parameters.json', 'application/json'), ('.caption.md', 'text/markdown'), ('.qa.json', 'application/json')]]
    if payload.get('plot_module_version') == '3.0' or any((plots / item[0]).is_file() for item in candidates):
        candidates.append((folder + '.provenance.json', 'application/json', 'provenance.run'))
    if stem == 'taxonomy_bray_curtis_pcoa':
        candidates += [('taxonomy_bray_curtis.' + part + '.tsv', 'text/tab-separated-values', 'figure.data') for part in ['distances', 'eigenvalues']]
    data_ids = []
    for filename, media, artifact_type in candidates:
        source = plots / filename
        if not _regular(source, work) or source.stat().st_size > 50_000_000:
            continue
        content = source.read_bytes()
        if media == 'image/svg+xml' and not _safe_svg(content):
            continue
        if media == 'application/pdf' and not content.startswith(b'%PDF-'):
            continue
        if media == 'application/json':
            try:
                json.loads(content)
            except (UnicodeError, ValueError):
                continue
        digest = hashlib.sha256(content).hexdigest()
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f'{task_id}/{folder}/{filename}/{digest}'))
        if artifact_type == 'figure.data' and filename.endswith('.tsv'):
            data_ids.append(identifier)
        if connection.execute('SELECT artifact_id FROM artifacts WHERE artifact_id=?', (identifier,)).fetchone():
            _refresh_figure_context(connection, identifier, payload)
            continue
        destination = output / 'stage_figures' / folder / digest / filename
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
        metadata = {'source': folder, 'category': kind.split('.')[1], 'stage_result': True,
                    'analysis_version': payload.get('analysis_version'), 'related_figure': name,
                    'provenance_file': folder + '.provenance.json',
                    'figure_skill': payload.get('figure_skill'), 'units': payload.get('units'),
                    'display_scale': payload.get('display_scale'), 'figure_plan': payload.get('figure_plan'),
                    'plot_module_version': payload.get('plot_module_version'),
                    'database_context': payload.get('database_context'),
                    'missingness_policy': payload.get('missingness_policy'),
                    'paper_sections': ['Method', 'Result']}
        record = dict(artifact_id=identifier, task_id=task_id, node_id=folder, producer=folder,
            artifact_type=artifact_type, schema_version='1.0', sample_scope='cohort',
            sample_id=None, cohort_id=task_id, media_type=media, path=str(destination), sha256=digest,
            size_bytes=len(content), metadata_json=json.dumps(metadata, ensure_ascii=False),
            downloadable=1, status='active', created_at=utc_now())
        connection.execute(f"INSERT INTO artifacts ({', '.join(record)}) VALUES ({', '.join('?' for _ in record)})", tuple(record.values()))
    # PNG is derived from its observed data; source data is never described as derived from a PNG.
    for parent_id in data_ids:
        connection.execute("INSERT OR IGNORE INTO artifact_relations VALUES (?, ?, 'derived_from')", (png_id, parent_id))


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
                        _refresh_figure_context(connection, artifact_id, payload, trace_hash=task_hash,
                            parameters={'top_n': payload.get('top_n')})
                        _publish_companions(connection, task_id=task_id, folder=folder, kind=artifact_type, plots=plots, work=work, output=output, name=name, payload=payload, png_id=artifact_id)
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
                        'provenance_file': folder + '.provenance.json',
                        'figure_skill': payload.get('figure_skill'),
                        'display_scale': payload.get('display_scale'), 'figure_plan': payload.get('figure_plan'),
                        'plot_module_version': payload.get('plot_module_version'),
                        'database_context': payload.get('database_context'),
                        'units': payload.get('units'),
                        'database_profile': payload.get('database_profile'),
                        'database_release': payload.get('database_release'),
                        'missingness_policy': payload.get('missingness_policy'),
                        'diversity': payload.get('diversity'), 'ordination': payload.get('ordination'),
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
                    _publish_companions(connection, task_id=task_id, folder=folder, kind=artifact_type, plots=plots, work=work, output=output, name=name, payload=payload, png_id=artifact_id)
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
        'Bray-Curtis PCoA requires at least two quantifiable samples': '至少需要两个具有非零物种丰度、可定量的样本才能生成 Bray–Curtis PCoA；当前条件不足，已跳过。',
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
            for revision in ('figures_v2', 'figures_v3'):
                revised = output / revision / folder / f'{folder}.provenance.json'
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
