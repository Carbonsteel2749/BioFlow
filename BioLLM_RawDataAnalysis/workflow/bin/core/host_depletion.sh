#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

sample=""; r1=""; r2=""; host_index=""; outdir=""; threads=4; task_id=manual; state_root=""
filter_mode="strict_both_unmapped"; min_retained_pairs=0; max_removed_pct=100; bowtie2_preset="very-sensitive"

usage() {
  cat >&2 <<'EOF'
Usage: host_depletion.sh --sample ID --r1 FILE --r2 FILE --host-index PREFIX --outdir DIR [options]

Options:
  --filter-mode strict_both_unmapped|concordant_unmapped
  --min-retained-pairs N       fail if fewer than N pairs remain (default: 0)
  --max-removed-pct PCT        fail if more than PCT percent is removed (default: 100)
  --bowtie2-preset PRESET      Bowtie2 preset, default: very-sensitive
  --threads N --task-id ID --state-root DIR --resume
EOF
}

while (($#)); do
  case "$1" in
    --sample) sample="${2:-}"; shift 2 ;;
    --r1) r1="${2:-}"; shift 2 ;;
    --r2) r2="${2:-}"; shift 2 ;;
    --host-index) host_index="${2:-}"; shift 2 ;;
    --outdir) outdir="${2:-}"; shift 2 ;;
    --threads) threads="${2:-}"; shift 2 ;;
    --task-id) task_id="${2:-}"; shift 2 ;;
    --state-root) state_root="${2:-}"; shift 2 ;;
    --filter-mode) filter_mode="${2:-}"; shift 2 ;;
    --min-retained-pairs) min_retained_pairs="${2:-}"; shift 2 ;;
    --max-removed-pct) max_removed_pct="${2:-}"; shift 2 ;;
    --bowtie2-preset) bowtie2_preset="${2:-}"; shift 2 ;;
    --resume) PIPELINE_RESUME=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 64 ;;
  esac
done

[[ -n "$sample" && -n "$r1" && -n "$r2" && -n "$host_index" && -n "$outdir" ]] || { usage; exit 64; }
[[ "$threads" =~ ^[1-9][0-9]*$ ]] || { echo "--threads must be a positive integer" >&2; exit 64; }
[[ "$min_retained_pairs" =~ ^[0-9]+$ ]] || { echo "--min-retained-pairs must be a non-negative integer" >&2; exit 64; }
python3 - "$max_removed_pct" <<'PY' || { echo "--max-removed-pct must be between 0 and 100" >&2; exit 64; }
import sys
try:
    value = float(sys.argv[1])
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if 0 <= value <= 100 else 1)
PY
case "$filter_mode" in strict_both_unmapped|concordant_unmapped) ;; *) echo "invalid --filter-mode: $filter_mode" >&2; exit 64 ;; esac

state_root="${state_root:-$(dirname "$outdir")}"; mkdir -p "$outdir"
export PIPELINE_TASK_ID="$task_id" PIPELINE_SAMPLE_ID="$sample" PIPELINE_LOG_DIR="$state_root/logs" PIPELINE_STATUS_DIR="$state_root/status"
step_prepare host_depletion
STEP_STARTED_AT="$(pipeline_now)"
export STEP_STARTED_AT

out1="$outdir/${sample}.R1.host_removed.fastq.gz"
out2="$outdir/${sample}.R2.host_removed.fastq.gz"
metrics="$outdir/${sample}.host_depletion.metrics.json"
tmpdir=""

host_fail() {
  local exit_code="${1:-1}" stage="${2:-runtime}" reason="${3:-unknown failure}" command="${4:-${CURRENT_COMMAND:-}}" line="${5:-}"
  trap - ERR
  [[ -n "${tmpdir:-}" && -d "$tmpdir" ]] && rm -rf -- "$tmpdir"
  log_message ERROR "failure_stage=$stage reason=$reason"
  status_write failed "$exit_code" "$stage: $reason" "$command" "$line"
  exit "$exit_code"
}

host_unexpected_failure() {
  local exit_code="$1" line="$2" command="$3"
  host_fail "$exit_code" runtime "unexpected shell failure" "$command" "$line"
}

trap 'host_unexpected_failure "$?" "$LINENO" "${BASH_COMMAND:-unknown}"' ERR

render_command() {
  local part rendered=""
  for part in "$@"; do printf -v part '%q' "$part"; rendered+="${rendered:+ }$part"; done
  printf '%s' "$rendered"
}

require_gzip_fastq() {
  local path="$1" label="$2"
  [[ -f "$path" && -s "$path" ]] || host_fail 66 input_presence "$label is missing or empty: $path"
  gzip -t -- "$path" >/dev/null 2>&1 || host_fail 65 input_integrity "$label is not a readable gzip FASTQ: $path"
}

fastq_pair_count() {
  python3 - "$1" "$2" <<'PY'
import gzip
import sys

def count(path):
    records = 0
    with gzip.open(path, "rt", encoding="utf-8", errors="strict") as handle:
        while True:
            header = handle.readline()
            if not header:
                return records
            sequence = handle.readline()
            plus = handle.readline()
            quality = handle.readline()
            if not (sequence and plus and quality):
                raise ValueError(f"incomplete FASTQ record in {path}")
            if not header.startswith("@") or not plus.startswith("+"):
                raise ValueError(f"invalid FASTQ structure in {path}")
            if len(sequence.rstrip("\r\n")) != len(quality.rstrip("\r\n")):
                raise ValueError(f"sequence/quality length mismatch in {path}")
            records += 1

left, right = count(sys.argv[1]), count(sys.argv[2])
if left != right:
    raise ValueError(f"paired FASTQ record count mismatch: R1={left}, R2={right}")
print(left)
PY
}

index_exists() {
  local extension shard
  for extension in bt2 bt2l; do
    local complete=1
    for shard in 1 2 3 4 rev.1 rev.2; do
      [[ -f "${host_index}.${shard}.${extension}" ]] || complete=0
    done
    (( complete )) && return 0
  done
  return 1
}

read_alignment_rate() {
  python3 - "$1" <<'PY'
import re
import sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
matches = re.findall(r"([0-9]+(?:\.[0-9]+)?)% overall alignment rate", text)
print(matches[-1] if matches else "null")
PY
}

outputs_are_complete() {
  local candidate_r1="$1" candidate_r2="$2" candidate_metrics="$3" current_input_pairs="$4"
  [[ -s "$candidate_r1" && -s "$candidate_r2" && -s "$candidate_metrics" ]] || return 1
  gzip -t -- "$candidate_r1" >/dev/null 2>&1 && gzip -t -- "$candidate_r2" >/dev/null 2>&1 || return 1
  local count
  count="$(fastq_pair_count "$candidate_r1" "$candidate_r2")" || return 1
  python3 - "$candidate_metrics" "$count" "$current_input_pairs" <<'PY'
import json
import sys
try:
    payload = json.load(open(sys.argv[1], encoding="utf-8"))
    output_pairs = int(sys.argv[2])
    current_input_pairs = int(sys.argv[3])
    assert payload["retained_pair_count"] == output_pairs
    assert payload["input_pair_count"] == current_input_pairs
    assert current_input_pairs >= output_pairs
    assert payload["filter_mode"] in {"strict_both_unmapped", "concordant_unmapped"}
except (AssertionError, KeyError, TypeError, ValueError, json.JSONDecodeError):
    raise SystemExit(1)
PY
}

require_command python3 || host_fail 127 dependency "python3 is required"
require_command gzip || host_fail 127 dependency "gzip is required"
require_gzip_fastq "$r1" "R1"
require_gzip_fastq "$r2" "R2"
input_pairs="$(fastq_pair_count "$r1" "$r2")" || host_fail 65 input_integrity "paired FASTQ is invalid or R1/R2 counts differ"

if checkpoint_valid "$out1" "$out2" "$metrics" && outputs_are_complete "$out1" "$out2" "$metrics" "$input_pairs"; then
  log_message INFO "resume_verified input_pairs=$input_pairs"
  exit 0
fi
if [[ "${PIPELINE_RESUME:-0}" == "1" ]]; then
  log_message WARN "resume_checkpoint_invalid reason=status_or_outputs_missing_or_corrupt; rerunning"
fi

step_start "human host read removal started filter_mode=$filter_mode"

require_value host_index "$host_index" || host_fail 64 parameter "host_index is required"
index_exists || host_fail 66 host_index "Bowtie2 index prefix is incomplete or unavailable: $host_index"
require_command bowtie2 || host_fail 127 dependency "bowtie2 is required"
if [[ "$filter_mode" == "strict_both_unmapped" ]]; then
  require_command samtools || host_fail 127 dependency "samtools is required for strict_both_unmapped mode"
fi

tmpdir="$(mktemp -d "$outdir/.host_depletion.${sample}.XXXXXX")" || host_fail 73 filesystem "unable to create temporary output directory"
trap '[[ -n "${tmpdir:-}" && -d "$tmpdir" ]] && rm -rf -- "$tmpdir"' EXIT
rm -f -- "$out1" "$out2" "$metrics"

summary="$tmpdir/bowtie2.summary.txt"
temp_out1="$tmpdir/${sample}.R1.host_removed.fastq.gz"
temp_out2="$tmpdir/${sample}.R2.host_removed.fastq.gz"
if [[ "$filter_mode" == "strict_both_unmapped" ]]; then
  raw_out1="$tmpdir/${sample}.R1.host_removed.fastq"
  raw_out2="$tmpdir/${sample}.R2.host_removed.fastq"
  command=(bowtie2 "--${bowtie2_preset}" -x "$host_index" -1 "$r1" -2 "$r2" --threads "$threads")
  CURRENT_COMMAND="$(render_command "${command[@]}") | samtools view -u -f 12 -F 2304 | samtools fastq -n -1 $(printf '%q' "$raw_out1") -2 $(printf '%q' "$raw_out2") -0 /dev/null -s /dev/null -"
  export CURRENT_COMMAND
  log_message INFO "command=$CURRENT_COMMAND"
  trap - ERR
  set +e
  "${command[@]}" 2>"$summary" | samtools view -u -f 12 -F 2304 | samtools fastq -n -1 "$raw_out1" -2 "$raw_out2" -0 /dev/null -s /dev/null -
  pipe_status=("${PIPESTATUS[@]}")
  set -e
  trap 'host_unexpected_failure "$?" "$LINENO" "${BASH_COMMAND:-unknown}"' ERR
  for line in "${pipe_status[@]}"; do [[ "$line" == 0 ]] || host_fail "$line" tool_execution "Bowtie2/samtools streaming command failed (pipeline status: ${pipe_status[*]})" "$CURRENT_COMMAND"; done
  while IFS= read -r line || [[ -n "$line" ]]; do log_message INFO "bowtie2_summary=$line"; done < "$summary"
  gzip -c -- "$raw_out1" > "$temp_out1" || host_fail 74 output_integrity "failed to compress retained R1"
  gzip -c -- "$raw_out2" > "$temp_out2" || host_fail 74 output_integrity "failed to compress retained R2"
else
  pattern="$tmpdir/${sample}.R%.host_removed.fastq.gz"
  command=(bowtie2 "--${bowtie2_preset}" --no-mixed --no-discordant -x "$host_index" -1 "$r1" -2 "$r2" --threads "$threads" --un-conc-gz "$pattern" -S /dev/null)
  CURRENT_COMMAND="$(render_command "${command[@]}")"; export CURRENT_COMMAND; log_message INFO "command=$CURRENT_COMMAND"
  trap - ERR; set +e; "${command[@]}" 2>"$summary"; tool_status=$?; set -e
  trap 'host_unexpected_failure "$?" "$LINENO" "${BASH_COMMAND:-unknown}"' ERR
  [[ "$tool_status" == 0 ]] || host_fail "$tool_status" tool_execution "Bowtie2 concordant-unmapped command failed" "$CURRENT_COMMAND"
  while IFS= read -r line || [[ -n "$line" ]]; do log_message INFO "bowtie2_summary=$line"; done < "$summary"
  temp_out1="$tmpdir/${sample}.R1.host_removed.fastq.gz"; temp_out2="$tmpdir/${sample}.R2.host_removed.fastq.gz"
fi

retained_pairs="$(fastq_pair_count "$temp_out1" "$temp_out2")" || host_fail 65 output_integrity "retained R1/R2 outputs are invalid or unpaired"
alignment_rate="$(read_alignment_rate "$summary")"
temp_metrics="$tmpdir/${sample}.host_depletion.metrics.json"
python3 - "$temp_metrics" "$sample" "$filter_mode" "$host_index" "$input_pairs" "$retained_pairs" "$alignment_rate" "$bowtie2_preset" <<'PY'
import json
import subprocess
import sys
from pathlib import Path

path, sample, mode, index, incoming, retained, alignment_rate, preset = sys.argv[1:]
incoming, retained = int(incoming), int(retained)
removed = incoming - retained
if removed < 0:
    raise SystemExit("retained pair count exceeds input pair count")
def pct(value): return round(100 * value / incoming, 6) if incoming else 0.0
def version(command):
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return result.stdout.splitlines()[0] if result.stdout else "unknown"
payload = {
    "sample_id": sample, "filter_mode": mode, "host_index_prefix": index,
    "input_pair_count": incoming, "retained_pair_count": retained, "removed_pair_count": removed,
    "retained_pct": pct(retained), "removed_pct": pct(removed),
    "bowtie2_overall_alignment_rate_pct": None if alignment_rate == "null" else float(alignment_rate),
    "bowtie2_preset": preset, "bowtie2_version": version(["bowtie2", "--version"]),
    "samtools_version": version(["samtools", "--version"]) if mode == "strict_both_unmapped" else None,
}
Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

outputs_are_complete "$temp_out1" "$temp_out2" "$temp_metrics" "$input_pairs" || host_fail 65 output_integrity "expected outputs are missing, corrupt, or metrics do not match retained reads"
python3 - "$retained_pairs" "$min_retained_pairs" "$input_pairs" "$max_removed_pct" <<'PY' || host_fail 65 threshold "retained reads violate configured host-depletion thresholds"
import sys
retained, minimum, incoming = map(int, sys.argv[1:4])
maximum_removed = float(sys.argv[4])
removed_pct = 100 * (incoming - retained) / incoming if incoming else 0
raise SystemExit(0 if retained >= minimum and removed_pct <= maximum_removed else 1)
PY

mv -- "$temp_out1" "$out1"; mv -- "$temp_out2" "$out2"; mv -- "$temp_metrics" "$metrics"
log_message INFO "metrics input_pairs=$input_pairs retained_pairs=$retained_pairs removed_pairs=$((input_pairs-retained_pairs)) alignment_rate_pct=$alignment_rate"
step_success "human host read removal completed filter_mode=$filter_mode" "$out1" "$out2" "$metrics"
