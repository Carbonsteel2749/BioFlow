#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if ! command -v nextflow >/dev/null 2>&1; then
  printf 'SKIP: nextflow is not installed; validate module smoke test not run\n'
  exit 0
fi

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
mkdir -p "$TMP_ROOT/input" "$TMP_ROOT/output"
printf '@read001/1\nACGT\n+\n!!!!\n' > "$TMP_ROOT/input/S01_R1.fastq"
printf '@read001/2\nACGT\n+\n!!!!\n' > "$TMP_ROOT/input/S01_R2.fastq"
printf 'sample_id,read1,read2\nS01,%s,%s\n' "$TMP_ROOT/input/S01_R1.fastq" "$TMP_ROOT/input/S01_R2.fastq" > "$TMP_ROOT/input/samples.csv"

nextflow run "$PROJECT_ROOT/workflow/validate_smoke.nf" \
  --input_manifest "$TMP_ROOT/input/samples.csv" \
  --outdir "$TMP_ROOT/output" \
  --task_id validate-nextflow-test \
  -work-dir "$TMP_ROOT/work"

test -s "$TMP_ROOT/output/validate/manifest.validated.csv"
python3 - "$TMP_ROOT/output/status/global.validate.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
if payload["status"] != "succeeded":
    raise SystemExit(payload)
PY
printf 'preprocessing validate Nextflow smoke test passed\n'
