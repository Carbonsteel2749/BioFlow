"""Publish successful DIY node products without claiming the whole run succeeded."""
from __future__ import annotations

import hashlib
import csv
import json
from pathlib import Path

from .artifacts import ArtifactService, SAFE_IDENTIFIER


TABLES = {
    'species_abundance.tsv': 'taxonomy.species_abundance',
    'bracken.species.tsv': 'taxonomy.bracken_abundance',
    'kraken.report': 'taxonomy.kraken_report',
    'read_genefamilies.tsv': 'functional.gene_families',
    'read_ko.tsv': 'functional.ko_abundance',
    'read_ec.tsv': 'functional.ec_abundance',
    'read_pathabundance.tsv': 'functional.pathway_abundance',
    'read_pathcoverage.tsv': 'functional.pathway_coverage',
    'mag_annotation.tsv': 'mag.annotation',
    'mag_taxonomy_function.tsv': 'mag.function_annotation',
    'mag_function_annotation.tsv': 'mag.function_annotation',
    'sample_mag_abundance.tsv': 'mag.sample_abundance',
    'sample_mag_function_abundance.tsv': 'mag.sample_function_abundance',
}


def _kind(path: Path, node_type: str):
    name = path.name
    if name in TABLES:
        return TABLES[name], 'text/tab-separated-values'
    if node_type == 'taxonomy' and name.endswith('.species.tsv'):
        return 'taxonomy.species_abundance', 'text/tab-separated-values'
    if node_type in {'fastp','fastqc'} and name.endswith(('.fastp.html','_fastqc.html','.fastp.json','_fastqc.zip')):
        return 'qc.report', {'.html':'text/html','.json':'application/json','.zip':'application/zip'}[path.suffix]
    if name.endswith('.provenance.json'):
        return 'provenance.run', 'application/json'
    if node_type == 'report' and name == 'multiqc_report.html':
        return 'report.multiqc', 'text/html'
    if node_type == 'report' and name in {'summary.json', 'summary.txt'}:
        return 'report.summary', 'application/json' if path.suffix == '.json' else 'text/plain'
    category = {'reads_plots': 'qc', 'taxonomy_plots': 'taxonomy',
                'functional_plots': 'functional', 'mag_plots': 'mag'}.get(node_type)
    if category:
        if path.suffix in {'.png', '.svg', '.pdf'}:
            return 'figure.' + category, {'.png': 'image/png', '.svg': 'image/svg+xml', '.pdf': 'application/pdf'}[path.suffix]
        if name.endswith(('.data.tsv', '.source.tsv', '.parameters.json', '.caption.md')):
            return 'figure.data', {'.tsv': 'text/tab-separated-values', '.json': 'application/json', '.md': 'text/markdown'}[path.suffix]
    return None


def publish_dynamic_artifacts(settings, repository, service: ArtifactService, task_id: str) -> list[dict]:
    if not SAFE_IDENTIFIER.fullmatch(task_id):
        return []
    run = repository.get_run(task_id)
    if run is None:
        return []
    try:
        summary = json.loads((Path(run['compiled_root']) / 'summary.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    roots = summary.get('node_output_roots', {})
    output = (settings.state_root / 'outputs' / task_id).absolute()
    if output.resolve() != output:
        return []
    existing = {(row['node_id'], row['path'], row['sha256']): row
                for row in service.repository.list_for_task(task_id)}
    sample_ids = set()
    try:
        with Path(run['input_manifest']).open(encoding='utf-8', newline='') as stream:
            sample_ids = {row['sample_id'] for row in csv.DictReader(stream) if row.get('sample_id')}
    except (OSError, TypeError, ValueError):
        pass
    published = []
    for node in run['nodes']:
        if node['status'] != 'succeeded':
            continue
        relative = roots.get(node['node_id'])
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
            continue
        folder = output / relative
        if folder.resolve() != folder or not folder.is_relative_to(output) or not folder.is_dir():
            continue
        for path in sorted(folder.rglob('*')):
            if not path.is_file() or path.resolve() != path:
                continue
            kind = _kind(path, node['node_type'])
            if kind is None:
                continue
            artifact_type, media = kind
            # Only generated image payloads, not arbitrary executable markup.
            if media in {'image/png', 'image/svg+xml', 'application/pdf', 'application/json'}:
                if path.stat().st_size > 50_000_000:
                    continue
                content = path.read_bytes()
                if media == 'image/png' and not content.startswith(b'\x89PNG\r\n\x1a\n'):
                    continue
                if media == 'application/pdf' and not content.startswith(b'%PDF-'):
                    continue
                if media == 'image/svg+xml':
                    from .stage_figures import _safe_svg
                    if not _safe_svg(content):
                        continue
                if media == 'application/json':
                    try:
                        json.loads(content)
                    except (ValueError, UnicodeError):
                        continue
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            sha = digest.hexdigest()
            key = (node['node_id'], str(path), sha)
            if key in existing:
                published.append(service.get(existing[key]['artifact_id']))
                continue
            record = service.register(task_id=task_id, node_id=node['node_id'],
                **_scope(path, folder, node['node_type'], sample_ids, task_id),
                producer='dynamic.' + node['node_type'], artifact_type=artifact_type,
                path=path, media_type=media,
                expected_sha256=sha, metadata={'stage_result': True, 'node_type': node['node_type'],
                    'node_id': node['node_id'], 'attempt': run.get('attempt', 1),
                    'run_status_at_publication': run['status'], 'relative_path': path.relative_to(output).as_posix()})
            published.append(record)
    return published


def _scope(path, folder, node_type, sample_ids, task_id):
    # Only sample-wise processes publish into an exact manifest sample folder.
    # MAG annotation remains cohort-level even if an identifier happens to match.
    if node_type in {'fastqc','fastp','host_depletion','taxonomy','functional_annotation'}:
        parts = path.relative_to(folder).parts
        if len(parts) >= 3 and parts[1] in sample_ids:
            return {'sample_scope':'sample', 'sample_id':parts[1]}
    return {'sample_scope':'cohort', 'cohort_id':task_id}
