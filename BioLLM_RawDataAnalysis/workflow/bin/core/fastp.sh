#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

sample=""
r1=""
r2=""
outdir=""
threads=4
task_id=manual
state_root=""
qualified_quality_phred=20
unqualified_percent_limit=40
n_base_limit=5
length_required=50
cut_front=true
cut_tail=true
cut_window_size=4
cut_mean_quality=20
trim_poly_g=true
correction=false
detect_adapter_for_pe=true

while (($#)); do
  case "$1" in
    --sample) sample="$2"; shift 2;;
    --r1) r1="$2"; shift 2;;
    --r2) r2="$2"; shift 2;;
    --outdir) outdir="$2"; shift 2;;
    --threads) threads="$2"; shift 2;;
    --qualified-quality-phred) qualified_quality_phred="$2"; shift 2;;
    --unqualified-percent-limit) unqualified_percent_limit="$2"; shift 2;;
    --n-base-limit) n_base_limit="$2"; shift 2;;
    --length-required) length_required="$2"; shift 2;;
    --cut-front) cut_front="$2"; shift 2;;
    --cut-tail) cut_tail="$2"; shift 2;;
    --cut-window-size) cut_window_size="$2"; shift 2;;
    --cut-mean-quality) cut_mean_quality="$2"; shift 2;;
    --trim-poly-g) trim_poly_g="$2"; shift 2;;
    --correction) correction="$2"; shift 2;;
    --detect-adapter-for-pe) detect_adapter_for_pe="$2"; shift 2;;
    --task-id) task_id="$2"; shift 2;;
    --state-root) state_root="$2"; shift 2;;
    --resume) PIPELINE_RESUME=1; shift;;
    *) echo "unknown argument: $1" >&2; exit 64;;
  esac
done

[[ -n "$sample" && -n "$r1" && -n "$r2" && -n "$outdir" ]] || {
  echo "--sample, --r1, --r2 and --outdir are required" >&2
  exit 64
}

state_root="${state_root:-$(dirname "$outdir")}"
mkdir -p "$outdir"
export PIPELINE_TASK_ID="$task_id" PIPELINE_SAMPLE_ID="$sample"
export PIPELINE_LOG_DIR="$state_root/logs" PIPELINE_STATUS_DIR="$state_root/status"
step_prepare fastp
trap 'fastp_fail "$?" runtime "unexpected shell failure at line $LINENO"' ERR

out1="$outdir/${sample}.R1.clean.fastq.gz"
out2="$outdir/${sample}.R2.clean.fastq.gz"
json="$outdir/${sample}.fastp.json"
html="$outdir/${sample}.fastp.html"
outputs=("$out1" "$out2" "$json" "$html")
temp_dir=""

cleanup() {
  if [[ -n "$temp_dir" && -d "$temp_dir" ]]; then
    rm -rf -- "$temp_dir"
  fi
}
trap cleanup EXIT

fastp_fail() {
  local exit_code="$1"
  local stage="$2"
  local reason="$3"
  trap - ERR
  log_message ERROR "fastp_failed exit_code=$exit_code stage=$stage sample=$sample reason=$reason"
  status_write failed "$exit_code" "stage=$stage sample=$sample reason=$reason" "${CURRENT_COMMAND:-fastp}" "${BASH_LINENO[0]:-0}"
  exit "$exit_code"
}

check_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1 || fastp_fail 127 dependency "$command_name is not available"
}

check_boolean() {
  local name="$1"
  local value="$2"
  [[ "$value" == true || "$value" == false ]] || fastp_fail 64 argument "$name must be true or false: $value"
}

check_positive_integer() {
  local name="$1"
  local value="$2"
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || fastp_fail 64 argument "$name must be a positive integer: $value"
}

check_nonnegative_integer() {
  local name="$1"
  local value="$2"
  [[ "$value" =~ ^[0-9]+$ ]] || fastp_fail 64 argument "$name must be a non-negative integer: $value"
}

check_fastq_input() {
  local label="$1"
  local path="$2"
  [[ -f "$path" && -r "$path" && -s "$path" ]] || fastp_fail 66 fastq_readable "$label is missing, unreadable or empty: $path"
  if [[ "$path" == *.gz ]] && ! gzip -t -- "$path"; then
    fastp_fail 65 fastq_gzip "$label gzip integrity check failed: $path"
  fi
}

fastp_json_stats() {
  local json_path="$1"
  python3 - "$json_path" 2>&1 <<'PY'
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    before = payload["summary"]["before_filtering"]["total_reads"]
    after = payload["summary"]["after_filtering"]["total_reads"]
except (OSError, ValueError, KeyError, TypeError) as error:
    raise SystemExit(f"invalid fastp JSON: {error}")

if not isinstance(before, int) or not isinstance(after, int):
    raise SystemExit("fastp total_reads values must be integers")
if before <= 0:
    raise SystemExit("fastp before_filtering.total_reads must be positive")
if after < 0 or after > before:
    raise SystemExit("fastp after_filtering.total_reads is outside the valid range")
print(f"{before}\t{after}\t{after / before * 100:.2f}")
PY
}

outputs_are_complete() {
  local candidate_r1="$1"
  local candidate_r2="$2"
  local candidate_json="$3"
  local candidate_html="$4"
  local report
  for report in "$candidate_r1" "$candidate_r2" "$candidate_json" "$candidate_html"; do
    if [[ ! -f "$report" || ! -s "$report" ]]; then
      FASTP_INTEGRITY_REASON="expected output is missing or empty: $report"
      return 1
    fi
  done
  if ! gzip -t -- "$candidate_r1"; then
    FASTP_INTEGRITY_REASON="R1 clean FASTQ gzip integrity check failed: $candidate_r1"
    return 1
  fi
  if ! gzip -t -- "$candidate_r2"; then
    FASTP_INTEGRITY_REASON="R2 clean FASTQ gzip integrity check failed: $candidate_r2"
    return 1
  fi
  local stats
  if ! stats="$(fastp_json_stats "$candidate_json")"; then
    FASTP_INTEGRITY_REASON="$stats"
    return 1
  fi
  FASTP_STATS="$stats"
}

if checkpoint_valid "${outputs[@]}"; then
  if outputs_are_complete "$out1" "$out2" "$json" "$html"; then
    exit 0
  fi
  log_message WARN "checkpoint_invalid reason=$FASTP_INTEGRITY_REASON; rerunning"
fi

step_start "adapter trimming and quality filtering started"

[[ "$sample" =~ ^[A-Za-z0-9._-]+$ ]] || fastp_fail 64 argument "sample contains unsupported characters: $sample"
check_positive_integer threads "$threads"
check_positive_integer qualified_quality_phred "$qualified_quality_phred"
check_nonnegative_integer unqualified_percent_limit "$unqualified_percent_limit"
(( unqualified_percent_limit <= 100 )) || fastp_fail 64 argument "unqualified_percent_limit must not exceed 100: $unqualified_percent_limit"
check_nonnegative_integer n_base_limit "$n_base_limit"
check_positive_integer length_required "$length_required"
check_positive_integer cut_window_size "$cut_window_size"
check_positive_integer cut_mean_quality "$cut_mean_quality"
check_boolean cut_front "$cut_front"
check_boolean cut_tail "$cut_tail"
check_boolean trim_poly_g "$trim_poly_g"
check_boolean correction "$correction"
check_boolean detect_adapter_for_pe "$detect_adapter_for_pe"
check_command fastp
check_command python3
check_command gzip
check_fastq_input R1 "$r1"
check_fastq_input R2 "$r2"

rm -f -- "${outputs[@]}"
temp_dir="$(mktemp -d "$outdir/.fastp.${sample}.XXXXXX")"
temp_out1="$temp_dir/${sample}.R1.clean.fastq.gz"
temp_out2="$temp_dir/${sample}.R2.clean.fastq.gz"
temp_json="$temp_dir/${sample}.fastp.json"
temp_html="$temp_dir/${sample}.fastp.html"

command=(
  fastp
  --in1 "$r1"
  --in2 "$r2"
  --out1 "$temp_out1"
  --out2 "$temp_out2"
  --json "$temp_json"
  --html "$temp_html"
  --thread "$threads"
  --qualified_quality_phred "$qualified_quality_phred"
  --unqualified_percent_limit "$unqualified_percent_limit"
  --n_base_limit "$n_base_limit"
  --length_required "$length_required"
  --cut_window_size "$cut_window_size"
  --cut_mean_quality "$cut_mean_quality"
)
[[ "$detect_adapter_for_pe" == true ]] && command+=(--detect_adapter_for_pe)
[[ "$cut_front" == true ]] && command+=(--cut_front)
[[ "$cut_tail" == true ]] && command+=(--cut_tail)
[[ "$trim_poly_g" == true ]] && command+=(--trim_poly_g)
[[ "$correction" == true ]] && command+=(--correction)

if run_tool "${command[@]}"; then
  :
else
  fastp_fail "$?" tool_execution "fastp command returned a non-zero exit code"
fi

if ! outputs_are_complete "$temp_out1" "$temp_out2" "$temp_json" "$temp_html"; then
  fastp_fail 66 output_integrity "$FASTP_INTEGRITY_REASON"
fi

mv -- "$temp_out1" "$out1"
mv -- "$temp_out2" "$out2"
mv -- "$temp_json" "$json"
mv -- "$temp_html" "$html"
IFS=$'\t' read -r reads_before reads_after retained_pct <<<"$FASTP_STATS"
log_message INFO "reads_before=$reads_before reads_after=$reads_after retained_pct=$retained_pct total_reads_unit=reads"
step_success "adapter trimming and quality filtering completed" "${outputs[@]}"
