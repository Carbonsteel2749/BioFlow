#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST_SCRIPT="$PROJECT_ROOT/workflow/bin/core/host_depletion.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
MOCK_BIN="$TMP_ROOT/mock-bin"
mkdir -p "$MOCK_BIN"

cat > "$MOCK_BIN/bowtie2" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
[[ "${HOST_MOCK_MODE:-success}" == "bowtie_fail" ]] && exit 23
[[ "${1:-}" == "--version" ]] && { echo 'bowtie2 mock 1.0'; exit 0; }
pattern=""
while (($#)); do
  case "$1" in --un-conc-gz) pattern="$2"; shift 2;; *) shift;; esac
done
if [[ -n "$pattern" ]]; then
  printf '@mock/1\nACGT\n+\n!!!!\n' | gzip -c > "${pattern//%/1}"
  printf '@mock/2\nTGCA\n+\n####\n' | gzip -c > "${pattern//%/2}"
else
  printf '@HD\tVN:1.6\n'
fi
printf '50.00%% overall alignment rate\n' >&2
MOCK
cat > "$MOCK_BIN/samtools" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "--version" ]] && { echo 'samtools mock 1.0'; exit 0; }
subcommand="$1"; shift
case "$subcommand" in
  view) cat ;;
  fastq)
    r1=""; r2=""
    while (($#)); do
      case "$1" in -1) r1="$2"; shift 2;; -2) r2="$2"; shift 2;; *) shift;; esac
    done
    cat >/dev/null
    printf '@mock/1\nACGT\n+\n!!!!\n' > "$r1"
    if [[ "${HOST_MOCK_MODE:-success}" == "output_mismatch" ]]; then : > "$r2"; else printf '@mock/2\nTGCA\n+\n####\n' > "$r2"; fi
    ;;
  *) exit 64 ;;
esac
MOCK
chmod +x "$MOCK_BIN/bowtie2" "$MOCK_BIN/samtools"

write_pair() {
  local directory="$1" stem="$2"
  mkdir -p "$directory"
  printf '@read001/1\nACGT\n+\n!!!!\n' | gzip -c > "$directory/${stem}_R1.fastq.gz"
  printf '@read001/2\nTGCA\n+\n####\n' | gzip -c > "$directory/${stem}_R2.fastq.gz"
}

write_index() {
  local prefix="$1" shard
  mkdir -p "$(dirname "$prefix")"
  for shard in 1 2 3 4 rev.1 rev.2; do : > "${prefix}.${shard}.bt2"; done
}

assert_status() {
  python3 - "$1" "$2" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding='utf-8'))
if payload['status'] != sys.argv[2]: raise SystemExit(payload)
PY
}

assert_failed_stage() {
  local root="$1" sample="$2" stage="$3"
  assert_status "$root/status/$sample.host_depletion.json" failed
  python3 - "$root/status/$sample.host_depletion.json" "$stage" <<'PY'
import json, sys
message = json.load(open(sys.argv[1], encoding='utf-8'))['message']
if not message.startswith(sys.argv[2] + ':'): raise SystemExit(message)
PY
}

run_host() {
  local sample="$1" r1="$2" r2="$3" root="$4" index="$5"
  shift 5
  PATH="$MOCK_BIN:$PATH" bash "$HOST_SCRIPT" --sample "$sample" --r1 "$r1" --r2 "$r2" \
    --host-index "$index" --outdir "$root/host/$sample" --threads 2 --task-id host-test --state-root "$root" "$@"
}

run_failure() {
  set +e; run_host "$@"; local code=$?; set -e; [[ "$code" -ne 0 ]]
}

INPUT="$TMP_ROOT/input"; OUTPUT="$TMP_ROOT/output"; INDEX="$TMP_ROOT/database/GRCh38"
mkdir -p "$INPUT" "$OUTPUT"
write_pair "$INPUT" S01
write_index "$INDEX"

run_host S01 "$INPUT/S01_R1.fastq.gz" "$INPUT/S01_R2.fastq.gz" "$OUTPUT" "$INDEX"
assert_status "$OUTPUT/status/S01.host_depletion.json" succeeded
for output in "$OUTPUT/host/S01/S01.R1.host_removed.fastq.gz" "$OUTPUT/host/S01/S01.R2.host_removed.fastq.gz" "$OUTPUT/host/S01/S01.host_depletion.metrics.json"; do test -s "$output"; done
gzip -t "$OUTPUT/host/S01/S01.R1.host_removed.fastq.gz"
python3 - "$OUTPUT/host/S01/S01.host_depletion.metrics.json" <<'PY'
import json, sys
p = json.load(open(sys.argv[1], encoding='utf-8'))
assert p['filter_mode'] == 'strict_both_unmapped'
assert p['input_pair_count'] == p['retained_pair_count'] == 1
assert p['removed_pair_count'] == 0
assert p['bowtie2_overall_alignment_rate_pct'] == 50.0
PY
! find "$OUTPUT/host/S01" -type f \( -name '*.sam' -o -name '*.bam' -o -name '*.cram' \) -print -quit | grep -q .

before="$(stat -c %Y "$OUTPUT/host/S01/S01.R1.host_removed.fastq.gz")"; sleep 1
run_host S01 "$INPUT/S01_R1.fastq.gz" "$INPUT/S01_R2.fastq.gz" "$OUTPUT" "$INDEX" --resume
after="$(stat -c %Y "$OUTPUT/host/S01/S01.R1.host_removed.fastq.gz")"
[[ "$before" == "$after" ]]
grep -F resume_verified "$OUTPUT/logs/S01.host_depletion.log" >/dev/null
rm "$OUTPUT/host/S01/S01.host_depletion.metrics.json"
run_host S01 "$INPUT/S01_R1.fastq.gz" "$INPUT/S01_R2.fastq.gz" "$OUTPUT" "$INDEX" --resume
test -s "$OUTPUT/host/S01/S01.host_depletion.metrics.json"

# A valid checkpoint must remain reusable when host depletion removes reads.
# The mock samtools command retains one pair, while this input contains two.
printf '@read001/1\nACGT\n+\n!!!!\n@read002/1\nCCCC\n+\n!!!!\n' | gzip -c > "$INPUT/S01R_R1.fastq.gz"
printf '@read001/2\nTGCA\n+\n####\n@read002/2\nGGGG\n+\n####\n' | gzip -c > "$INPUT/S01R_R2.fastq.gz"
RESUME_FILTERED_ROOT="$OUTPUT/resume-filtered"
run_host S01R "$INPUT/S01R_R1.fastq.gz" "$INPUT/S01R_R2.fastq.gz" "$RESUME_FILTERED_ROOT" "$INDEX"
python3 - "$RESUME_FILTERED_ROOT/host/S01R/S01R.host_depletion.metrics.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["input_pair_count"] == 2
assert payload["retained_pair_count"] == 1
PY
filtered_before="$(stat -c %Y "$RESUME_FILTERED_ROOT/host/S01R/S01R.R1.host_removed.fastq.gz")"
sleep 1
run_host S01R "$INPUT/S01R_R1.fastq.gz" "$INPUT/S01R_R2.fastq.gz" "$RESUME_FILTERED_ROOT" "$INDEX" --resume
filtered_after="$(stat -c %Y "$RESUME_FILTERED_ROOT/host/S01R/S01R.R1.host_removed.fastq.gz")"
[[ "$filtered_before" == "$filtered_after" ]] || {
  echo "filtered host-depletion checkpoint was rerun instead of resumed" >&2
  exit 1
}

PATH="/usr/bin:/bin" bash "$HOST_SCRIPT" --sample S02 --r1 "$INPUT/S01_R1.fastq.gz" --r2 "$INPUT/S01_R2.fastq.gz" \
  --host-index "$INDEX" --outdir "$OUTPUT/missing-tool/host/S02" --task-id host-test --state-root "$OUTPUT/missing-tool" || true
assert_failed_stage "$OUTPUT/missing-tool" S02 dependency

BOWTIE_ONLY="$TMP_ROOT/bowtie-only"; mkdir -p "$BOWTIE_ONLY"; cp "$MOCK_BIN/bowtie2" "$BOWTIE_ONLY/bowtie2"
PATH="$BOWTIE_ONLY:/usr/bin:/bin" bash "$HOST_SCRIPT" --sample S02B --r1 "$INPUT/S01_R1.fastq.gz" --r2 "$INPUT/S01_R2.fastq.gz" \
  --host-index "$INDEX" --outdir "$OUTPUT/missing-samtools/host/S02B" --task-id host-test --state-root "$OUTPUT/missing-samtools" || true
assert_failed_stage "$OUTPUT/missing-samtools" S02B dependency

run_failure S03 "$INPUT/S01_R1.fastq.gz" "$INPUT/S01_R2.fastq.gz" "$OUTPUT/index-missing" "$TMP_ROOT/missing/GRCh38"
assert_failed_stage "$OUTPUT/index-missing" S03 host_index

HOST_MOCK_MODE=bowtie_fail run_failure S04 "$INPUT/S01_R1.fastq.gz" "$INPUT/S01_R2.fastq.gz" "$OUTPUT/tool-fail" "$INDEX"
assert_failed_stage "$OUTPUT/tool-fail" S04 tool_execution

HOST_MOCK_MODE=output_mismatch run_failure S05 "$INPUT/S01_R1.fastq.gz" "$INPUT/S01_R2.fastq.gz" "$OUTPUT/output-mismatch" "$INDEX"
assert_failed_stage "$OUTPUT/output-mismatch" S05 output_integrity

SPECIAL_DIR="$INPUT/path with \$(touch host-injected)"
write_pair "$SPECIAL_DIR" S06
run_host S06 "$SPECIAL_DIR/S06_R1.fastq.gz" "$SPECIAL_DIR/S06_R2.fastq.gz" "$OUTPUT/special" "$INDEX" --filter-mode concordant_unmapped
assert_status "$OUTPUT/special/status/S06.host_depletion.json" succeeded
test ! -e "$PROJECT_ROOT/host-injected"

printf 'preprocessing host depletion tests passed\n'
