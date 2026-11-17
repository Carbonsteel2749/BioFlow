"""Bounded, source-linked intake for BioLLM metagenomic result packages."""
from __future__ import annotations

import csv
import json
import math
import sqlite3
from pathlib import Path

from article_writing.contracts import AnalysisBundle


def report_root(path: Path) -> Path | None:
    if (path / 'tables/read_counts.tsv').is_file():
        return path
    candidates = [p for p in sorted((path / 'nodes').glob('*'))
                  if (p / 'tables/read_counts.tsv').is_file()]
    if len(candidates) > 1:
        raise ValueError('Multiple report nodes found; select the intended report directory.')
    return candidates[0] if candidates else None


def task_name(task: Path) -> str:
    database = task.parent.parent / 'state/tasks.sqlite3'
    if not database.is_file():
        return task.name
    try:
        with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
            for table, key in [('workflow_runs', 'task_id'), ('tasks', 'id')]:
                try:
                    row = db.execute(f'SELECT name FROM {table} WHERE {key}=?', (task.name,)).fetchone()
                    if row and row[0]:
                        return row[0]
                except sqlite3.OperationalError:
                    continue
    except sqlite3.Error:
        pass
    return task.name


def rows(path: Path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        yield from csv.DictReader(stream, delimiter='\t')


def inspect_materials(root: Path) -> dict:
    root = root.resolve()
    facts, issues, tables = [], [], []

    def fact(kind, value, path, row=None, column=None, sample=None, unit=None):
        entry = {'id': f'fact_{len(facts)+1:04d}', 'kind': kind, 'value': value,
                 'source': {'file': str(path.relative_to(root)), 'row': row, 'column': column},
                 'sample_id': sample, 'unit': unit}
        facts.append(entry)

    qc = root / 'tables/read_counts.tsv'
    sample_ids, seen = [], set()
    for n, row in enumerate(rows(qc), 2):
        if n > 10002:
            raise ValueError('QC table exceeds supported 10000 samples; split the package.')
        sample = (row.get('sample_id') or '').strip()
        if not sample or sample in seen:
            raise ValueError(f'Invalid or duplicate sample_id in read_counts.tsv row {n}')
        seen.add(sample); sample_ids.append(sample)
        for col in ['raw_reads', 'post_fastp_reads', 'post_fastp_pairs', 'post_host_pairs', 'host_removed_pairs']:
            value = row.get(col)
            if value in (None, ''):
                issues.append(f'{sample}: missing {col}')
                continue
            try:
                number = float(value)
                if not math.isfinite(number) or number < 0 or not number.is_integer():
                    raise ValueError()
            except ValueError:
                raise ValueError(f'Invalid count at read_counts.tsv:{n}:{col}')
            fact('qc', int(number), qc, n, col, sample, 'read_pairs' if 'pairs' in col else 'reads')
    if not sample_ids:
        raise ValueError('No samples found in read_counts.tsv')

    # Enumerate headers only: a 604 MB gene-family table is never materialized.
    for path in sorted((root / 'tables').rglob('*')):
        if not path.is_file() or not (path.suffix == '.tsv' or '.tsv.' in path.name):
            continue
        with path.open(encoding='utf-8-sig', errors='replace') as stream:
            header = stream.readline(16384).rstrip('\r\n').split('\t')
        tables.append({'file': str(path.relative_to(root)), 'size_bytes': path.stat().st_size,
                       'columns': header, 'inspection': 'header_only'})

    species = root / 'tables/species_abundance.tsv'
    if species.is_file():
        # Streaming per-sample maximum; never average incompatible units or compare groups.
        best = {}
        for n, row in enumerate(rows(species), 2):
            sample, unit = row.get('sample_id', ''), row.get('abundance_unit', '')
            if sample not in seen or not unit:
                continue
            try:
                value = float(row.get('abundance', ''))
            except ValueError:
                continue
            if not math.isfinite(value) or value < 0:
                continue
            key = (sample, unit)
            if key not in best or value > best[key][0]:
                best[key] = (value, n, row.get('taxonomy', ''), row.get('taxid', ''))
        for (sample, unit), (value, n, taxon, taxid) in sorted(best.items()):
            fact('descriptive_taxonomy', {'taxon': taxon, 'taxid': taxid, 'abundance': value,
                 'selection': 'maximum reported abundance within this sample and unit; not a group comparison'},
                 species, n, 'abundance', sample, unit)

    for name in ['software_versions.tsv', 'database_versions.tsv']:
        path = root / 'provenance' / name
        if path.is_file():
            for n, row in enumerate(rows(path), 2):
                if n > 102:
                    issues.append(f'{name}: only first 100 entries inspected'); break
                fact('methods', row, path, n)
    for name in ['parameters.json', 'run.json']:
        path = root / 'provenance' / name
        if path.is_file() and path.stat().st_size < 131072:
            fact('methods', json.loads(path.read_text(encoding='utf-8')), path)
    summary = next((root / d / 'summary.json' for d in ['report', 'reports']
                    if (root / d / 'summary.json').is_file()), None)
    if summary and summary.stat().st_size < 131072:
        fact('reported_inventory', json.loads(summary.read_text(encoding='utf-8')), summary)

    figures = [str(p.relative_to(root)) for p in sorted((root / 'figures').rglob('*'))
               if p.is_file() and p.suffix.lower() in {'.png', '.svg', '.pdf', '.jpg'}]
    missing = [
        {'id': 'study_design', 'status': 'needs_confirmation', 'message': '研究问题、样本来源、分组和混杂因素尚未经研究者确认。'},
        {'id': 'statistical_evidence', 'status': 'not_validated', 'message': '尚未验证组间统计检验、效应量及多重检验校正；不得生成显著差异结论。'},
        {'id': 'experimental_methods', 'status': 'needs_confirmation', 'message': '采样、DNA提取、建库、伦理等实验信息需补充或确认，不由分析日志推断。'},
    ]
    if not figures:
        missing.append({'id': 'figures', 'status': 'not_found', 'message': '未发现 figures/ 下的独立论文图件；HTML质控报告不等于论文图件。'})
    return {'schema_version': 1, 'package_root': str(root), 'mode': 'descriptive_draft',
            'sample_count': len(sample_ids), 'sample_ids': sample_ids, 'facts': facts,
            'tables': tables, 'figures': figures, 'missing': missing, 'issues': issues,
            'allowed': ['有来源的方法描述', '质控结果', '样本内物种丰度描述', '功能结果文件清单'],
            'unsupported': ['组间显著差异', '因果机制', '未提供的多样性分析', '临床结论'],
            'scope_note': '表格清单仅检查表头；功能表未进行全量统计，不能将文件存在解释为差异或富集发现。'}


def material_instructions(report: dict, section: str) -> str:
    selected = [f for f in report['facts'] if
                (f['kind'] == 'methods' if section == 'methods' else
                 f['kind'] in {'qc', 'descriptive_taxonomy', 'reported_inventory'})]
    # Exact structured facts carry sources; no prefix truncation of result tables.
    return ('MATERIAL CONTRACT: descriptive draft only. Do not infer group differences, statistical '
            'significance, causality, diversity, enrichment, or wet-lab procedures. A file inventory '
            'or row count is not a biological finding. Preserve units and distinguish reads from '
            'read pairs. Preserve HUMAnN_reported units; do not convert them to percentages. '
            'UNMAPPED/UNGROUPED/UNINTEGRATED are not biological pathways. '
            'Describe only verified facts; identify missing information instead of filling it in. '
            'Methods may include procedures or statistical methods ONLY if explicitly supported.\n'
            + json.dumps({'sample_count': report['sample_count'], 'facts': selected,
                          'missing': report['missing']}, ensure_ascii=False))


def materials_bundle(root: Path, report: dict) -> AnalysisBundle:
    from article_writing.adapters.archive_catalog import CatalogItem, attach_catalog
    raw = {'source': 'biollm_materials_v1', 'package_root': str(root), 'materials': report}
    methods = {f['id']: json.dumps(f['value'], ensure_ascii=False)
               for f in report['facts'] if f['kind'] == 'methods'}
    bundle = AnalysisBundle(summary=f"Descriptive metagenomic package: {report['sample_count']} samples.",
                          metrics={'sample_count': report['sample_count']}, methods=methods,
                          limitations=[m['message'] for m in report['missing']],
                          evidence_ids=[f['id'] for f in report['facts']], raw=raw)
    grouped = {}
    for f in report['facts']:
        grouped.setdefault(f['source']['file'], []).append(f)
    catalog = [CatalogItem(item_id=f'cat_material_{n}', kind='parameter' if fs[0]['kind'] == 'methods' else 'summary',
                           relative_path=name, path=str(root / name), label=name,
                           excerpt=json.dumps(fs, ensure_ascii=False))
               for n, (name, fs) in enumerate(grouped.items())]
    # Preserve genuine existing displays on legacy packages; never invent a figure.
    for n, name in enumerate(report['figures']):
        catalog.append(CatalogItem(item_id=f'cat_figures_material_{n}', kind='figure',
                                   relative_path=name, path=str(root / name),
                                   label=Path(name).stem, excerpt=Path(name).name))
    return attach_catalog(bundle, catalog)
