#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FASTQC_SCRIPT="$PROJECT_ROOT/workflow/bin/core/fastqc.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
MOCK_BIN="$TMP_ROOT/mock-bin"
mkdir -p "$MOCK_BIN"

write_mock_fastqc() {
  cat > "$MOCK_BIN/fastqc" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${FASTQC_MOCK_MODE:-success}" == "tool_fail" ]]; then
  exit 23
fi
outdir=""
threads=""
inputs=()
while (($#)); do
  case "$1" in
    --outdir) outdir="$2"; shift 2;;
    --threads) threads="$2"; shift 2;;
    *) inputs+=("$1"); shift;;
  esac
done
[[ "$threads" == "${FASTQC_EXPECT_THREADS:-2}" ]] || exit 24
mkdir -p "$outdir"
for input in "${inputs[@]}"; do
  base="$(basename "$input")"
  base="${base%.gz}"; base="${base%.fastq}"; base="${base%.fq}"
  if [[ "${FASTQC_MOCK_MODE:-success}" != "missing_html" ]]; then
    printf '<html>FastQC</html>\n' > "$outdir/${base}_fastqc.html"
  fi
  if [[ "${FASTQC_MOCK_MODE:-success}" == "missing_zip" ]]; then
    continue
  fi
  if [[ "${FASTQC_MOCK_MODE:-success}" == "corrupt_zip" ]]; then
    printf 'not a zip' > "$outdir/${base}_fastqc.zip"
    continue
  fi
  python3 - "$outdir/${base}_fastqc.zip" "$base" "${FASTQC_MOCK_MODE:-success}" <<'PY'
import sys
import zipfile

target, stem, mode = sys.argv[1:]
with zipfile.ZipFile(target, "w") as archive:
    archive.writestr(f"{stem}_fastqc/summary.txt", "PASS\tBasic Statistics\n")
    if mode != "missing_data":
        archive.writestr(f"{stem}_fastqc/fastqc_data.txt", ">>Basic Statistics\tpass\n")
PY
done
MOCK
  chmod +x "$MOCK_BIN/fastqc"
}

write_fastq_pair() {
  local directory="$1"
  local stem="$2"
  mkdir -p "$directory"
  printf '@read001/1\nACGT\n+\n!!!!\n' > "$directory/${stem}_R1.fastq"
  printf '@read001/2\nTGCA\n+\n####\n' > "$directory/${stem}_R2.fastq"
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
  local sample="$2"
  local stage="$3"
  assert_status "$output_root/status/$sample.fastqc_raw.json" failed
  python3 - "$output_root/status/$sample.fastqc_raw.json" "$stage" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    message = json.load(handle)["message"]
if f"stage={sys.argv[2]}" not in message:
    raise SystemExit(message)
PY
}

run_expect_failure() {
  local sample="$1"
  local r1="$2"
  local r2="$3"
  local output_root="$4"
  set +e
  PATH="$MOCK_BIN:$PATH" FASTQC_EXPECT_THREADS=2 \
    bash "$FASTQC_SCRIPT" --sample "$sample" --r1 "$r1" --r2 "$r2" \
      --outdir "$output_root/fastqc/$sample" --threads 2 --task-id fastqc-test --state-root "$output_root"
  local code=$?
  set -e
  [[ "$code" -ne 0 ]]
}

write_mock_fastqc
INPUT="$TMP_ROOT/input"
OUTPUT="$TMP_ROOT/output"
mkdir -p "$INPUT" "$OUTPUT"
write_fastq_pair "$INPUT" S01

PATH="$MOCK_BIN:$PATH" FASTQC_EXPECT_THREADS=2 \
  bash "$FASTQC_SCRIPT" --sample S01 --r1 "$INPUT/S01_R1.fastq" --r2 "$INPUT/S01_R2.fastq" \
    --outdir "$OUTPUT/fastqc/S01" --threads 2 --task-id fastqc-test --state-root "$OUTPUT"
assert_status "$OUTPUT/status/S01.fastqc_raw.json" succeeded
for report in "$OUTPUT/fastqc/S01"/*_fastqc.html "$OUTPUT/fastqc/S01"/*_fastqc.zip; do
  test -s "$report"
done
test "$(find "$OUTPUT/fastqc/S01" -name '*_fastqc.html' | wc -l)" -eq 2
test "$(find "$OUTPUT/fastqc/S01" -name '*_fastqc.zip' | wc -l)" -eq 2
python3 - "$OUTPUT/fastqc/S01/S01_R1_fastqc.zip" <<'PY'
import sys
import zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    assert archive.testzip() is None
PY

before="$(stat -c %Y "$OUTPUT/fastqc/S01/S01_R1_fastqc.zip")"
sleep 1
PATH="$MOCK_BIN:$PATH" FASTQC_EXPECT_THREADS=2 \
  bash "$FASTQC_SCRIPT" --sample S01 --r1 "$INPUT/S01_R1.fastq" --r2 "$INPUT/S01_R2.fastq" \
    --outdir "$OUTPUT/fastqc/S01" --threads 2 --task-id fastqc-test --state-root "$OUTPUT" --resume
after="$(stat -c %Y "$OUTPUT/fastqc/S01/S01_R1_fastqc.zip")"
[[ "$before" == "$after" ]]
grep -F checkpoint_hit "$OUTPUT/logs/S01.fastqc_raw.log" >/dev/null

rm "$OUTPUT/fastqc/S01/S01_R2_fastqc.zip"
PATH="$MOCK_BIN:$PATH" FASTQC_EXPECT_THREADS=2 \
  bash "$FASTQC_SCRIPT" --sample S01 --r1 "$INPUT/S01_R1.fastq" --r2 "$INPUT/S01_R2.fastq" \
    --outdir "$OUTPUT/fastqc/S01" --threads 2 --task-id fastqc-test --state-root "$OUTPUT" --resume
test -s "$OUTPUT/fastqc/S01/S01_R2_fastqc.zip"

set +e
PATH="/usr/bin:/bin" bash "$FASTQC_SCRIPT" --sample S02 --r1 "$INPUT/S01_R1.fastq" --r2 "$INPUT/S01_R2.fastq" \
  --outdir "$OUTPUT/dependency/fastqc/S02" --threads 2 --task-id fastqc-test --state-root "$OUTPUT/dependency"
dependency_code=$?
set -e
[[ "$dependency_code" -ne 0 ]]
assert_failed_stage "$OUTPUT/dependency" S02 dependency

FASTQC_MOCK_MODE=tool_fail run_expect_failure S03 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" "$OUTPUT/tool-fail"
assert_failed_stage "$OUTPUT/tool-fail" S03 tool_execution

printf 'not-gzip' > "$INPUT/S04_R1.fastq.gz"
printf '@read001/2\nACGT\n+\n!!!!\n' | gzip -c > "$INPUT/S04_R2.fastq.gz"
run_expect_failure S04 "$INPUT/S04_R1.fastq.gz" "$INPUT/S04_R2.fastq.gz" "$OUTPUT/bad-gzip"
assert_failed_stage "$OUTPUT/bad-gzip" S04 fastq_gzip

FASTQC_MOCK_MODE=missing_html run_expect_failure S05 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" "$OUTPUT/missing-html"
assert_failed_stage "$OUTPUT/missing-html" S05 output_integrity

FASTQC_MOCK_MODE=corrupt_zip run_expect_failure S06 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" "$OUTPUT/corrupt-zip"
assert_failed_stage "$OUTPUT/corrupt-zip" S06 output_integrity

FASTQC_MOCK_MODE=missing_data run_expect_failure S07 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" "$OUTPUT/missing-data"
assert_failed_stage "$OUTPUT/missing-data" S07 output_integrity

SPECIAL_DIR="$INPUT/path with \$(touch fastqc-injected)"
write_fastq_pair "$SPECIAL_DIR" S08
PATH="$MOCK_BIN:$PATH" FASTQC_EXPECT_THREADS=2 \
  bash "$FASTQC_SCRIPT" --sample S08 --r1 "$SPECIAL_DIR/S08_R1.fastq" --r2 "$SPECIAL_DIR/S08_R2.fastq" \
    --outdir "$OUTPUT/special/fastqc/S08" --threads 2 --task-id fastqc-test --state-root "$OUTPUT/special"
assert_status "$OUTPUT/special/status/S08.fastqc_raw.json" succeeded
test ! -e "$PROJECT_ROOT/fastqc-injected"

printf 'preprocessing FastQC tests passed\n'
