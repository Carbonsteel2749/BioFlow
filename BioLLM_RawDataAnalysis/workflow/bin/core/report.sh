#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

results_root=""
outdir=""
task_id="manual"
state_root=""
nextflow_work_root=""
database_manifest=""
input_manifest=""
parameters_base64=""
run_name="unknown"
project_root=""

while (($#)); do
  case "$1" in
    --results-root) results_root="$2"; shift 2 ;;
    --outdir) outdir="$2"; shift 2 ;;
    --task-id) task_id="$2"; shift 2 ;;
    --state-root) state_root="$2"; shift 2 ;;
    --nextflow-work-root) nextflow_work_root="$2"; shift 2 ;;
    --database-manifest) database_manifest="$2"; shift 2 ;;
    --input-manifest) input_manifest="$2"; shift 2 ;;
    --parameters-base64) parameters_base64="$2"; shift 2 ;;
    --run-name) run_name="$2"; shift 2 ;;
    --project-root) project_root="$2"; shift 2 ;;
    --resume) PIPELINE_RESUME=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 64 ;;
  esac
done

[[ -n "$results_root" && -n "$outdir" && -n "$state_root" ]] || {
  echo "--results-root, --outdir and --state-root are required" >&2
  exit 64
}
[[ -n "$nextflow_work_root" ]] || {
  echo "--nextflow-work-root is required" >&2
  exit 64
}
[[ -n "$database_manifest" && -n "$input_manifest" ]] || {
  echo "--database-manifest and --input-manifest are required" >&2
  exit 64
}
[[ -n "$parameters_base64" && -n "$project_root" ]] || {
  echo "--parameters-base64 and --project-root are required" >&2
  exit 64
}

tables_dir="tables"
provenance_dir="provenance"
deliverables_dir="deliverables"
report="$outdir/multiqc_report.html"
archive="$deliverables_dir/${task_id}.tar.gz"
run_record="$provenance_dir/run.json"
read_counts="$tables_dir/read_counts.tsv"
summary_json="$outdir/summary.json"
report_entry="$outdir/README.txt"

mkdir -p "$outdir" "$tables_dir" "$provenance_dir" "$deliverables_dir"
export PIPELINE_TASK_ID="$task_id"
export PIPELINE_SAMPLE_ID="global"
export PIPELINE_LOG_DIR="$state_root/logs"
export PIPELINE_STATUS_DIR="$state_root/status"
step_prepare report
trap 'step_fail "$?" "$LINENO" "${CURRENT_COMMAND:-$BASH_COMMAND}"' ERR

if checkpoint_valid \
  "$report" \
  "$archive" \
  "$run_record" \
  "$read_counts" \
  "$summary_json" \
  "$report_entry"
then
  exit 0
fi

step_start "result aggregation and packaging started"
require_command multiqc
require_command python3
require_dir "$results_root"
require_file "$database_manifest"
require_file "$input_manifest"

run_tool multiqc "$results_root" \
  --outdir "$outdir" \
  --filename multiqc_report.html \
  --force

run_tool python3 "$SCRIPT_DIR/package_results.py" \
  --task-id "$task_id" \
  --results-root "$results_root" \
  --report-dir "$outdir" \
  --tables-dir "$tables_dir" \
  --provenance-dir "$provenance_dir" \
  --archive "$archive" \
  --state-root "$state_root" \
  --nextflow-work-root "$nextflow_work_root" \
  --input-manifest "$input_manifest" \
  --database-manifest "$database_manifest" \
  --exclude-artifact "$database_manifest" \
  --parameters-base64 "$parameters_base64" \
  --run-name "$run_name" \
  --project-root "$project_root"

step_success \
  "result aggregation and packaging completed" \
  "$report" "$archive" "$run_record" "$read_counts" "$summary_json" "$report_entry"
