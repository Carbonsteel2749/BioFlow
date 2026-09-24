#!/usr/bin/env bash
set -euo pipefail

if ! command -v nextflow >/dev/null 2>&1; then
  printf 'SKIP: nextflow is not available in this environment\n'
  exit 0
fi
if ! command -v bowtie2 >/dev/null 2>&1 || ! command -v samtools >/dev/null 2>&1; then
  printf 'SKIP: bowtie2 and samtools are required for host-depletion Nextflow smoke test\n'
  exit 0
fi
if [[ -z "${HOST_INDEX_PREFIX:-}" ]]; then
  printf 'SKIP: set HOST_INDEX_PREFIX to a complete external Bowtie2 human-index prefix\n'
  exit 0
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
printf '@read001/1\nACGT\n+\n!!!!\n' | gzip -c > "$TMP_ROOT/S01_R1.fastq.gz"
printf '@read001/2\nTGCA\n+\n####\n' | gzip -c > "$TMP_ROOT/S01_R2.fastq.gz"
printf 'sample_id,read1,read2\nS01,%s,%s\n' "$TMP_ROOT/S01_R1.fastq.gz" "$TMP_ROOT/S01_R2.fastq.gz" > "$TMP_ROOT/manifest.csv"

cd "$PROJECT_ROOT/workflow"
nextflow run host_depletion_smoke.nf -work-dir "$TMP_ROOT/work" \
  --input_manifest "$TMP_ROOT/manifest.csv" --host_index "$HOST_INDEX_PREFIX" --outdir "$TMP_ROOT/results"
test -s "$TMP_ROOT/results/host_depletion/S01/S01.host_depletion.metrics.json"
printf 'host-depletion Nextflow smoke test passed\n'
