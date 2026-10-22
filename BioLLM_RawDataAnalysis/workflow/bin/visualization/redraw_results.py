#!/usr/bin/env python3
"""Offline redraw entrypoint. It reads only a packaged normalized results directory."""
from __future__ import annotations
import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def functional_table(tables, name, scratch):
    flat = tables / (name + '.tsv')
    if flat.is_file():
        return flat
    directory = tables / {'ko': 'ko_abundance', 'ec': 'ec_abundance'}.get(name, name)
    files = sorted(p for p in directory.rglob('*') if p.is_file() and '.tsv' in p.name) if directory.is_dir() else []
    if not files:
        return flat  # Caller records unavailable inputs instead of guessing.
    scratch.mkdir(parents=True, exist_ok=True)
    target = scratch / (name + '.tsv')
    header = None
    with target.open('w', newline='') as out:
        writer = csv.writer(out, delimiter='\t')
        for path in files:
            with path.open(newline='') as handle:
                reader = csv.reader(handle, delimiter='\t')
                current = next(reader, [])
                if header is None:
                    header = current
                    writer.writerow(header)
                if current != header:
                    raise ValueError(f'{name}: incompatible normalized table headers')
                writer.writerows(reader)
    return target

def invoke(script: str, args: list[object]) -> int:
    return subprocess.run([sys.executable, str(ROOT / script), *map(str, args)], check=False).returncode

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--top-n", type=int, default=20)
    args = parser.parse_args()
    tables = args.results_dir / "tables"
    args.outdir.mkdir(parents=True, exist_ok=True)
    jobs = []
    reads = tables / "read_counts.tsv"
    qc_metrics = args.results_dir / 'figures' / 'qc' / 'reads_input.json'
    if qc_metrics.is_file():
        reads = qc_metrics
    species = tables / "species_abundance.tsv"
    if reads.is_file():
        jobs.append(("reads_plots.py", ["--input", reads, "--outdir", args.outdir / "reads"]))
    if species.is_file():
        jobs.append(("taxonomy_plots.py", ["--input", species, "--outdir", args.outdir / "taxonomy", "--top-n", args.top_n]))
    functional = {name: functional_table(tables, name, args.outdir / 'normalized_inputs') for name in ("gene_families", "ko", "ec", "pathway_abundance", "pathway_coverage")}
    notices = []
    if all(path.is_file() for path in functional.values()):
        jobs.append(("functional_plots.py", ["--gene-families", functional["gene_families"], "--ko", functional["ko"], "--ec", functional["ec"], "--pathway-abundance", functional["pathway_abundance"], "--pathway-coverage", functional["pathway_coverage"], "--outdir", args.outdir / "functional", "--top-n", args.top_n]))
    else:
        notices.append({'analysis': 'functional', 'status': 'skipped', 'missing_inputs': [name for name, path in functional.items() if not path.is_file()]})
    mag, abundance = tables / "mag_annotation.tsv", tables / "sample_mag_abundance.tsv"
    if mag.is_file() and abundance.is_file():
        jobs.append(("mag_plots.py", ["--mag-annotation", mag, "--sample-mag-abundance", abundance, "--outdir", args.outdir / "mag", "--top-n", args.top_n]))
    if not jobs:
        parser.error("no supported normalized result tables were found; FASTQ inputs are intentionally unsupported")
    codes = [invoke(script, command) for script, command in jobs]
    (args.outdir / 'redraw.summary.json').write_text(json.dumps({'jobs': [j[0] for j in jobs], 'exit_codes': codes, 'notices': notices}, indent=2))
    return max(codes)

if __name__ == "__main__":
    raise SystemExit(main())
