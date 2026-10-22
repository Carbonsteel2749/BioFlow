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

while (($#)); do
  case "$1" in
    --sample) sample="$2"; shift 2;;
    --r1) r1="$2"; shift 2;;
    --r2) r2="$2"; shift 2;;
    --outdir) outdir="$2"; shift 2;;
    --threads) threads="$2"; shift 2;;
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
step_prepare fastqc_raw

fastqc_stem() {
  local value
  value="$(basename "$1")"
  value="${value%.gz}"
  value="${value%.fastq}"
  value="${value%.fq}"
  printf '%s' "$value"
}

fastqc_fail() {
  local exit_code="$1"
  local stage="$2"
  local reason="$3"
  trap - ERR
  log_message ERROR "fastqc_failed exit_code=$exit_code stage=$stage sample=$sample reason=$reason"
  status_write failed "$exit_code" "stage=$stage sample=$sample reason=$reason" "${CURRENT_COMMAND:-fastqc}" "${BASH_LINENO[0]:-0}"
  exit "$exit_code"
}

check_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1 || fastqc_fail 127 dependency "$command_name is not available"
}

check_fastq_input() {
  local label="$1"
  local path="$2"
  [[ -f "$path" && -r "$path" && -s "$path" ]] || fastqc_fail 66 fastq_readable "$label is missing, unreadable or empty: $path"
  if [[ "$path" == *.gz ]]; then
    if ! gzip -t -- "$path"; then
      fastqc_fail 65 fastq_gzip "$label gzip integrity check failed: $path"
    fi
  fi
}

zip_has_required_fastqc_files() {
  local zip_path="$1"
  local check_result
  if ! check_result="$(python3 - "$zip_path" 2>&1 <<'PY'
import sys
import zipfile

path = sys.argv[1]
try:
    with zipfile.ZipFile(path) as archive:
        corrupted_member = archive.testzip()
        if corrupted_member:
            raise ValueError(f"corrupt member: {corrupted_member}")
        names = archive.namelist()
except (OSError, ValueError, zipfile.BadZipFile) as error:
    raise SystemExit(str(error))

for required in ("summary.txt", "fastqc_data.txt"):
    if not any(name == required or name.endswith("/" + required) for name in names):
        raise SystemExit(f"missing {required}")
PY
)"; then
    FASTQC_INTEGRITY_REASON="ZIP validation failed for $zip_path: ${check_result:-unknown ZIP error}"
    return 1
  fi
}

reports_are_complete() {
  local html1="$1"
  local zip1="$2"
  local html2="$3"
  local zip2="$4"
  local report
  for report in "$html1" "$html2" "$zip1" "$zip2"; do
    if [[ ! -f "$report" || ! -s "$report" ]]; then
      FASTQC_INTEGRITY_REASON="expected report is missing or empty: $report"
      return 1
    fi
  done
  zip_has_required_fastqc_files "$zip1" || return 1
  zip_has_required_fastqc_files "$zip2" || return 1
}

stem1="$(fastqc_stem "$r1")"
stem2="$(fastqc_stem "$r2")"
html1="$outdir/${stem1}_fastqc.html"
zip1="$outdir/${stem1}_fastqc.zip"
html2="$outdir/${stem2}_fastqc.html"
zip2="$outdir/${stem2}_fastqc.zip"
outputs=("$html1" "$zip1" "$html2" "$zip2")

if checkpoint_valid "${outputs[@]}"; then
  if reports_are_complete "$html1" "$zip1" "$html2" "$zip2"; then
    exit 0
  fi
  log_message WARN "checkpoint_invalid reason=$FASTQC_INTEGRITY_REASON; rerunning"
fi

step_start "raw read quality assessment started threads=$threads"

[[ "$threads" =~ ^[1-9][0-9]*$ ]] || fastqc_fail 64 argument "threads must be a positive integer: $threads"
check_command fastqc
check_command python3
check_command gzip
check_fastq_input R1 "$r1"
check_fastq_input R2 "$r2"

rm -f -- "${outputs[@]}"
if run_tool fastqc --threads "$threads" --outdir "$outdir" "$r1" "$r2"; then
  :
else
  fastqc_fail "$?" tool_execution "FastQC command returned a non-zero exit code"
fi

if ! reports_are_complete "$html1" "$zip1" "$html2" "$zip2"; then
  fastqc_fail 66 output_integrity "$FASTQC_INTEGRITY_REASON"
fi

step_success "raw read quality assessment completed" "${outputs[@]}"
