#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FASTP_SCRIPT="$PROJECT_ROOT/workflow/bin/core/fastp.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
MOCK_BIN="$TMP_ROOT/mock-bin"
mkdir -p "$MOCK_BIN"

write_mock_fastp() {
  cat > "$MOCK_BIN/fastp" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${FASTP_MOCK_MODE:-success}" == "tool_fail" ]]; then
  exit 23
fi
[[ -n "${FASTP_ARGS_FILE:-}" ]] && printf '%s\n' "$@" > "$FASTP_ARGS_FILE"
out1=""; out2=""; json=""; html=""
while (($#)); do
  case "$1" in
    --out1) out1="$2"; shift 2;;
    --out2) out2="$2"; shift 2;;
    --json) json="$2"; shift 2;;
    --html) html="$2"; shift 2;;
    *) shift;;
  esac
done
if [[ "${FASTP_MOCK_MODE:-success}" != "missing_r1" ]]; then
  if [[ "${FASTP_MOCK_MODE:-success}" == "corrupt_gzip" ]]; then
    printf 'not a gzip' > "$out1"
  else
    printf '@read001/1\nACGT\n+\n!!!!\n' | gzip -c > "$out1"
  fi
fi
printf '@read001/2\nTGCA\n+\n####\n' | gzip -c > "$out2"
if [[ "${FASTP_MOCK_MODE:-success}" == "bad_json" ]]; then
  printf '{not-json' > "$json"
elif [[ "${FASTP_MOCK_MODE:-success}" == "missing_stats" ]]; then
  printf '{"summary":{"before_filtering":{"total_reads":2}}}' > "$json"
else
  printf '{"summary":{"before_filtering":{"total_reads":2},"after_filtering":{"total_reads":1}}}' > "$json"
fi
if [[ "${FASTP_MOCK_MODE:-success}" != "missing_html" ]]; then
  printf '<html>fastp</html>\n' > "$html"
fi
MOCK
  chmod +x "$MOCK_BIN/fastp"
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
  assert_status "$output_root/status/$sample.fastp.json" failed
  python3 - "$output_root/status/$sample.fastp.json" "$stage" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    message = json.load(handle)["message"]
if f"stage={sys.argv[2]}" not in message:
    raise SystemExit(message)
PY
}

run_fastp() {
  local sample="$1"
  local r1="$2"
  local r2="$3"
  local output_root="$4"
  shift 4
  PATH="$MOCK_BIN:$PATH" \
    bash "$FASTP_SCRIPT" --sample "$sample" --r1 "$r1" --r2 "$r2" \
      --outdir "$output_root/fastp/$sample" --threads 2 --task-id fastp-test --state-root "$output_root" "$@"
}

run_expect_failure() {
  local sample="$1"
  local r1="$2"
  local r2="$3"
  local output_root="$4"
  shift 4
  set +e
  run_fastp "$sample" "$r1" "$r2" "$output_root" "$@"
  local code=$?
  set -e
  [[ "$code" -ne 0 ]]
}

write_mock_fastp
INPUT="$TMP_ROOT/input"
OUTPUT="$TMP_ROOT/output"
mkdir -p "$INPUT" "$OUTPUT"
write_fastq_pair "$INPUT" S01
ARGS_FILE="$TMP_ROOT/fastp.args"
FASTP_ARGS_FILE="$ARGS_FILE" run_fastp S01 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" "$OUTPUT"
assert_status "$OUTPUT/status/S01.fastp.json" succeeded
for output in "$OUTPUT/fastp/S01/S01.R1.clean.fastq.gz" "$OUTPUT/fastp/S01/S01.R2.clean.fastq.gz" "$OUTPUT/fastp/S01/S01.fastp.json" "$OUTPUT/fastp/S01/S01.fastp.html"; do
  test -s "$output"
done
gzip -t "$OUTPUT/fastp/S01/S01.R1.clean.fastq.gz"
grep -Fx -- '--qualified_quality_phred' "$ARGS_FILE" >/dev/null
grep -Fx -- '20' "$ARGS_FILE" >/dev/null
grep -Fx -- '--length_required' "$ARGS_FILE" >/dev/null
grep -Fx -- '50' "$ARGS_FILE" >/dev/null
grep -Fx -- '--detect_adapter_for_pe' "$ARGS_FILE" >/dev/null
grep -Fx -- '--cut_front' "$ARGS_FILE" >/dev/null
grep -F 'reads_before=2 reads_after=1 retained_pct=50.00' "$OUTPUT/logs/S01.fastp.log" >/dev/null

before="$(stat -c %Y "$OUTPUT/fastp/S01/S01.R1.clean.fastq.gz")"
sleep 1
run_fastp S01 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" "$OUTPUT" --resume
after="$(stat -c %Y "$OUTPUT/fastp/S01/S01.R1.clean.fastq.gz")"
[[ "$before" == "$after" ]]
grep -F checkpoint_hit "$OUTPUT/logs/S01.fastp.log" >/dev/null

rm "$OUTPUT/fastp/S01/S01.fastp.html"
run_fastp S01 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" "$OUTPUT" --resume
test -s "$OUTPUT/fastp/S01/S01.fastp.html"

set +e
PATH="/usr/bin:/bin" bash "$FASTP_SCRIPT" --sample S02 --r1 "$INPUT/S01_R1.fastq" --r2 "$INPUT/S01_R2.fastq" \
  --outdir "$OUTPUT/dependency/fastp/S02" --threads 2 --task-id fastp-test --state-root "$OUTPUT/dependency"
dependency_code=$?
set -e
[[ "$dependency_code" -ne 0 ]]
assert_failed_stage "$OUTPUT/dependency" S02 dependency

FASTP_MOCK_MODE=tool_fail run_expect_failure S03 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" "$OUTPUT/tool-fail"
assert_failed_stage "$OUTPUT/tool-fail" S03 tool_execution

printf 'not-gzip' > "$INPUT/S04_R1.fastq.gz"
printf '@read001/2\nACGT\n+\n!!!!\n' | gzip -c > "$INPUT/S04_R2.fastq.gz"
run_expect_failure S04 "$INPUT/S04_R1.fastq.gz" "$INPUT/S04_R2.fastq.gz" "$OUTPUT/bad-gzip"
assert_failed_stage "$OUTPUT/bad-gzip" S04 fastq_gzip

FASTP_MOCK_MODE=corrupt_gzip run_expect_failure S05 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" "$OUTPUT/corrupt-output"
assert_failed_stage "$OUTPUT/corrupt-output" S05 output_integrity

FASTP_MOCK_MODE=bad_json run_expect_failure S06 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" "$OUTPUT/bad-json"
assert_failed_stage "$OUTPUT/bad-json" S06 output_integrity

FASTP_MOCK_MODE=missing_stats run_expect_failure S07 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" "$OUTPUT/missing-stats"
assert_failed_stage "$OUTPUT/missing-stats" S07 output_integrity

FASTP_MOCK_MODE=missing_html run_expect_failure S08 "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" "$OUTPUT/missing-html"
assert_failed_stage "$OUTPUT/missing-html" S08 output_integrity

SPECIAL_DIR="$INPUT/path with \$(touch fastp-injected)"
write_fastq_pair "$SPECIAL_DIR" S09
run_fastp S09 "$SPECIAL_DIR/S09_R1.fastq" "$SPECIAL_DIR/S09_R2.fastq" "$OUTPUT/special" --length-required 75 --correction true
assert_status "$OUTPUT/special/status/S09.fastp.json" succeeded
test ! -e "$PROJECT_ROOT/fastp-injected"

printf 'preprocessing fastp tests passed\n'
