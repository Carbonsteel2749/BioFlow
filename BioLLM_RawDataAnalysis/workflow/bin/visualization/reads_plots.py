#!/usr/bin/env python3
"""Adapt native metrics/packaged tables to the shared QC renderer and Artifact contract."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from common import QCValidationError
from qc_plots import build_figures
from plot_utils import FIGURE_SKILL, styled_plots


def metrics_records(root):
    records = {}
    for path in sorted(root.rglob('*.fastp.json')):
        sample = path.name.removesuffix('.fastp.json')
        item = records.setdefault(sample, {'sample_id': sample})
        if 'raw_reads' in item:
            raise QCValidationError('duplicate fastp sample metrics')
        payload = json.loads(path.read_text())
        summary = payload['summary']
        after = summary['after_filtering']
        item.update(raw_reads=summary['before_filtering']['total_reads'], post_fastp_reads=after['total_reads'])
        for key in ('q20_rate', 'q30_rate', 'gc_content'):
            if key in after:
                item[key] = float(after[key]) * 100  # fastp JSON defines fractions.
        if 'rate' in payload.get('duplication', {}):
            item['duplication_rate'] = float(payload['duplication']['rate']) * 100
    for path in sorted(root.rglob('*.host_depletion.metrics.json')):
        payload = json.loads(path.read_text())
        sample = str(payload['sample_id'])
        item = records.setdefault(sample, {'sample_id': sample})
        if 'post_host_pairs' in item:
            raise QCValidationError('duplicate host sample metrics')
        item.update(post_fastp_pairs=payload['input_pair_count'], post_host_pairs=payload['retained_pair_count'],
                    host_removed_pairs=payload['removed_pair_count'])
    if not records:
        raise QCValidationError('no native preprocessing metrics found')
    return [records[k] for k in sorted(records)]


@styled_plots
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--input', type=Path)
    source.add_argument('--metrics-dir', type=Path)
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)
    provenance = {'analysis': 'reads_plots', 'analysis_version': '2.1', 'plots': {},
                  'unit_note': 'Read counts are reads; retained host pairs are multiplied by 2. fastp JSON fractions are converted explicitly to percent.',
                  'applicability': 'Descriptive QC only; no group inference.'}
    try:
        path = args.input
        if args.metrics_dir:
            path = args.outdir / 'reads_input.json'
            path.write_text(json.dumps(metrics_records(args.metrics_dir), indent=2))
        with tempfile.TemporaryDirectory(prefix='qc-render-') as temporary:
            result = build_figures(path, temporary)
            provenance['input'] = result['input']
            for record in result['figures']:
                name = record['name']
                stem = name if name.startswith('reads_') else 'reads_' + name
                folder = Path(temporary) / 'figures' / name
                for suffix in ('png', 'svg', 'pdf'):
                    source_file = folder / ('figure.' + suffix)
                    if source_file.is_file():
                        shutil.copyfile(source_file, args.outdir / (stem + '.' + suffix))
                shutil.copyfile(folder / 'figure.data.tsv', args.outdir / (stem + '.source.tsv'))
                shutil.copyfile(folder / 'figure.provenance.json', args.outdir / (stem + '.parameters.json'))
                provenance['plots'][name] = {'status': 'generated' if record['status'] == 'written' else 'skipped',
                    'output': stem + '.png' if record['status'] == 'written' else None, 'reason': record['reason']}
        provenance['status'] = 'completed'
    except (OSError, ValueError, KeyError, TypeError) as exc:
        provenance.update(status='failed', reason=str(exc))
    provenance['figure_skill'] = dict(FIGURE_SKILL)
    (args.outdir / 'reads_plots.provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    return 0 if provenance['status'] == 'completed' else 65


if __name__ == '__main__':
    raise SystemExit(main())
