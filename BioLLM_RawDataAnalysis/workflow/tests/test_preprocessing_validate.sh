#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VALIDATE="$PROJECT_ROOT/workflow/bin/core/validate.sh"
ASSERTIONS="$PROJECT_ROOT/workflow/tests/helpers/assert_integration_outputs.py"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

write_fastq_pair() {
  local directory="$1"
  local stem="$2"
  local r1_name="${3:-${stem}_R1.fastq}"
  local r2_name="${4:-${stem}_R2.fastq}"
  mkdir -p "$directory"
  printf '@read001/1\nACGT\n+\n!!!!\n@read002/1\nTGCA\n+\n####\n' > "$directory/$r1_name"
  printf '@read001/2\nACGT\n+\n!!!!\n@read002/2\nTGCA\n+\n####\n' > "$directory/$r2_name"
}

write_manifest() {
  local path="$1"
  local sample="$2"
  local r1="$3"
  local r2="$4"
  printf 'sample_id,read1,read2\n%s,%s,%s\n' "$sample" "$r1" "$r2" > "$path"
}

assert_status() {
  local path="$1"
  local expected="$2"
  python3 - "$path" "$expected" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
if payload["status"] != sys.argv[2]:
    raise SystemExit(payload)
PY
}

assert_failed_stage() {
  local output_root="$1"
  local stage="$2"
  local expected_sample="${3:-global}"
  assert_status "$output_root/status/global.validate.json" failed
  python3 - "$output_root/status/global.validate.json" "$stage" "$expected_sample" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
message = payload["message"]
if f"stage={sys.argv[2]}" not in message:
    raise SystemExit(f"missing stage in status message: {message}")
if payload["sample_id"] != sys.argv[3]:
    raise SystemExit(f"unexpected sample_id: {payload}")
PY
}

run_expect_failure() {
  local manifest="$1"
  local output_root="$2"
  set +e
  bash "$VALIDATE" --manifest "$manifest" --outdir "$output_root/validate" --task-id validate-test --state-root "$output_root"
  local code=$?
  set -e
  [[ "$code" -ne 0 ]]
}

INPUT="$TMP_ROOT/input"
OUTPUT="$TMP_ROOT/output"
mkdir -p "$INPUT" "$OUTPUT"

write_fastq_pair "$INPUT" S01
write_manifest "$INPUT/valid.csv" S01 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq"
bash "$VALIDATE" --manifest "$INPUT/valid.csv" --outdir "$OUTPUT/validate" --task-id validate-test --state-root "$OUTPUT"
assert_status "$OUTPUT/status/global.validate.json" succeeded
test -s "$OUTPUT/validate/manifest.validated.csv"
python3 "$ASSERTIONS" manifest "$OUTPUT/validate/manifest.validated.csv" S01
grep -F "sample=S01 r1_reads=2 r2_reads=2" "$OUTPUT/logs/global.validate.log" >/dev/null

printf '@SRR33675950.1 A00111:761:HJMWHDSX2:1:1101:3902:1016/1\nACGT\n+\n!!!!\n' > "$INPUT/ENA_R1.fastq"
printf '@SRR33675950.1 A00111:761:HJMWHDSX2:1:1101:3902:1016/2\nTGCA\n+\n####\n' > "$INPUT/ENA_R2.fastq"
write_manifest "$INPUT/ena-header.csv" ENA "$INPUT/ENA_R1.fastq" "$INPUT/ENA_R2.fastq"
bash "$VALIDATE" --manifest "$INPUT/ena-header.csv" --outdir "$OUTPUT/ena-header/validate" --task-id validate-ena --state-root "$OUTPUT/ena-header"
assert_status "$OUTPUT/ena-header/status/global.validate.json" succeeded
python3 "$ASSERTIONS" manifest "$OUTPUT/ena-header/validate/manifest.validated.csv" ENA

RELATIVE_ROOT="$TMP_ROOT/relative-case"
mkdir -p "$RELATIVE_ROOT/manifests" "$RELATIVE_ROOT/reads"
write_fastq_pair "$RELATIVE_ROOT/reads" S07
write_manifest "$RELATIVE_ROOT/manifests/samples.csv" S07 \
  "../reads/S07_R1.fastq" "../reads/S07_R2.fastq"
(
  cd "$TMP_ROOT"
  bash "$VALIDATE" \
    --manifest "$RELATIVE_ROOT/manifests/samples.csv" \
    --outdir "$OUTPUT/relative/validate" \
    --task-id validate-relative \
    --state-root "$OUTPUT/relative"
)
assert_status "$OUTPUT/relative/status/global.validate.json" succeeded
python3 "$ASSERTIONS" manifest "$OUTPUT/relative/validate/manifest.validated.csv" S07

before="$(stat -c %Y "$OUTPUT/validate/manifest.validated.csv")"
sleep 1
bash "$VALIDATE" --manifest "$INPUT/valid.csv" --outdir "$OUTPUT/validate" --task-id validate-test --state-root "$OUTPUT" --resume
after="$(stat -c %Y "$OUTPUT/validate/manifest.validated.csv")"
[[ "$before" == "$after" ]]
grep -F checkpoint_hit "$OUTPUT/logs/global.validate.log" >/dev/null

rm "$OUTPUT/validate/manifest.validated.csv"
bash "$VALIDATE" --manifest "$INPUT/valid.csv" --outdir "$OUTPUT/validate" --task-id validate-test --state-root "$OUTPUT" --resume
test -s "$OUTPUT/validate/manifest.validated.csv"

printf 'wrong,header\nS01,a,b\n' > "$INPUT/header.csv"
run_expect_failure "$INPUT/header.csv" "$OUTPUT/header"
assert_failed_stage "$OUTPUT/header" manifest_header

write_manifest "$INPUT/duplicate.csv" S01 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq"
printf 'S01,%s,%s\n' "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" >> "$INPUT/duplicate.csv"
run_expect_failure "$INPUT/duplicate.csv" "$OUTPUT/duplicate"
assert_failed_stage "$OUTPUT/duplicate" manifest_sample_id S01

write_manifest "$INPUT/missing.csv" S02 "$INPUT/missing_R1.fastq" "$INPUT/missing_R2.fastq"
run_expect_failure "$INPUT/missing.csv" "$OUTPUT/missing"
assert_failed_stage "$OUTPUT/missing" manifest_file S02

printf 'not-a-gzip-stream' > "$INPUT/S03_R1.fastq.gz"
printf '@read001/2\nACGT\n+\n!!!!\n' | gzip -c > "$INPUT/S03_R2.fastq.gz"
write_manifest "$INPUT/bad-gzip.csv" S03 "$INPUT/S03_R1.fastq.gz" "$INPUT/S03_R2.fastq.gz"
run_expect_failure "$INPUT/bad-gzip.csv" "$OUTPUT/bad-gzip"
assert_failed_stage "$OUTPUT/bad-gzip" fastq_gzip S03

printf '@different/2\nACGT\n+\n!!!!\n' > "$INPUT/S04_R2.fastq"
printf '@read001/1\nACGT\n+\n!!!!\n' > "$INPUT/S04_R1.fastq"
write_manifest "$INPUT/unpaired.csv" S04 "$INPUT/S04_R1.fastq" "$INPUT/S04_R2.fastq"
run_expect_failure "$INPUT/unpaired.csv" "$OUTPUT/unpaired"
assert_failed_stage "$OUTPUT/unpaired" fastq_pairing S04

printf '@read001/1\nACGT\n+\n!!!!\n@read002/1\nTGCA\n+\n####\n' > "$INPUT/S05_R1.fastq"
printf '@read001/2\nACGT\n+\n!!!!\n' > "$INPUT/S05_R2.fastq"
write_manifest "$INPUT/count-mismatch.csv" S05 "$INPUT/S05_R1.fastq" "$INPUT/S05_R2.fastq"
run_expect_failure "$INPUT/count-mismatch.csv" "$OUTPUT/count-mismatch"
assert_failed_stage "$OUTPUT/count-mismatch" fastq_count S05

SPECIAL_DIR="$INPUT/path with \$(touch injected)"
write_fastq_pair "$SPECIAL_DIR" S06
write_manifest "$INPUT/special.csv" S06 "$SPECIAL_DIR/S06_R1.fastq" "$SPECIAL_DIR/S06_R2.fastq"
bash "$VALIDATE" --manifest "$INPUT/special.csv" --outdir "$OUTPUT/special/validate" --task-id validate-test --state-root "$OUTPUT/special"
assert_status "$OUTPUT/special/status/global.validate.json" succeeded
test ! -e "$PROJECT_ROOT/injected"

printf 'preprocessing validate tests passed\n'
