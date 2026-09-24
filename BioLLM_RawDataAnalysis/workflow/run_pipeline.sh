#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/bin/lib/common.sh"
manifest=""; outdir=""; task_id=""; database_registry=""; database_profile=""; resume=0; enable_mags=false; enable_reassembly=true; min_free_gb="${PIPELINE_MIN_FREE_GB:-0}"
threads=4; mag_threads=8; mag_memory_gb=32; assembler=megahit; bin_completeness=70; bin_contamination=5; nextflow_bin="${NEXTFLOW_BIN:-nextflow}"
host_index=""; host_filter_mode="strict_both_unmapped"; host_min_retained_pairs=0; host_max_removed_pct=100; host_bowtie2_preset="very-sensitive"
while (($#)); do case "$1" in
 --manifest) manifest="$2"; shift 2;; --outdir) outdir="$2"; shift 2;; --task-id) task_id="$2"; shift 2;;
 --database-registry) database_registry="$2"; shift 2;; --database-profile) database_profile="$2"; shift 2;;
 --resume) resume=1; PIPELINE_RESUME=1; shift;; --enable-mags) enable_mags=true; shift;; --disable-reassembly) enable_reassembly=false; shift;;
 --threads) threads="$2"; shift 2;; --mag-threads) mag_threads="$2"; shift 2;; --mag-memory-gb) mag_memory_gb="$2"; shift 2;;
 --assembler) assembler="$2"; shift 2;; --bin-completeness) bin_completeness="$2"; shift 2;; --bin-contamination) bin_contamination="$2"; shift 2;;
 --host-index) host_index="$2"; shift 2;; --host-filter-mode) host_filter_mode="$2"; shift 2;;
 --host-min-retained-pairs) host_min_retained_pairs="$2"; shift 2;; --host-max-removed-pct) host_max_removed_pct="$2"; shift 2;;
 --host-bowtie2-preset) host_bowtie2_preset="$2"; shift 2;; --nextflow-bin) nextflow_bin="$2"; shift 2;;
 --min-free-gb) min_free_gb="$2"; shift 2;;
 *) echo "unknown argument: $1" >&2; exit 64;; esac; done
[[ -n "$manifest" && -n "$outdir" && -n "$database_registry" && -n "$database_profile" ]] || { echo "--manifest, --outdir, --database-registry and --database-profile are required" >&2; exit 64; }
manifest="$(realpath -e -- "$manifest")"
database_registry="$(realpath -e -- "$database_registry")"
mkdir -p -- "$outdir"
outdir="$(cd "$outdir" && pwd -P)"
task_id="${task_id:-$(python3 -c 'import uuid; print(uuid.uuid4())')}"
[[ "$min_free_gb" =~ ^[0-9]+$ ]] || { echo "--min-free-gb must be a non-negative integer" >&2; exit 64; }
export PIPELINE_TASK_ID="$task_id" PIPELINE_SAMPLE_ID="global" PIPELINE_LOG_DIR="$outdir/logs" PIPELINE_STATUS_DIR="$outdir/status"
step_prepare pipeline; trap 'step_fail "$?" "$LINENO" "${CURRENT_COMMAND:-$BASH_COMMAND}"' ERR
available_kb="$(df -Pk "$outdir" | awk 'NR == 2 {print $4}')"
required_kb=$((min_free_gb * 1024 * 1024))
if (( available_kb < required_kb )); then
  log_message ERROR "insufficient_disk_space available_kb=$available_kb required_kb=$required_kb"
  status_write failed 75 "insufficient_disk_space available_kb=$available_kb required_kb=$required_kb" "df -Pk $outdir" "$LINENO"
  exit 75
fi
database_manifest="$outdir/.pipeline/database.resolved.json"
database_args=(--registry "$database_registry" --profile "$database_profile" --output "$database_manifest")
[[ "$enable_mags" == true ]] && database_args+=(--enable-mags)
run_tool python3 "$SCRIPT_DIR/bin/core/validate_databases.py" "${database_args[@]}"
step_start "Nextflow orchestration started enable_mags=$enable_mags resume=$resume"
require_command "$nextflow_bin"; require_file "$manifest"; require_file "$database_manifest"; require_file "$SCRIPT_DIR/main.nf"
command=("$nextflow_bin" run "$SCRIPT_DIR/main.nf" --input_manifest "$manifest" --outdir "$outdir" --task_id "$task_id" --database_registry "$database_registry" --database_profile "$database_profile" --database_manifest "$database_manifest" --enable_mags "$enable_mags" --enable_reassembly "$enable_reassembly" --threads "$threads" --mag_threads "$mag_threads" --mag_memory_gb "$mag_memory_gb" --assembler "$assembler" --bin_completeness "$bin_completeness" --bin_contamination "$bin_contamination" --host_index "$host_index" --host_filter_mode "$host_filter_mode" --host_min_retained_pairs "$host_min_retained_pairs" --host_max_removed_pct "$host_max_removed_pct" --host_bowtie2_preset "$host_bowtie2_preset" -work-dir "$outdir/work" -with-trace "$outdir/trace.tsv" -with-timeline "$outdir/timeline.html" -with-report "$outdir/nextflow-report.html")
[[ "$resume" == 1 ]] && command+=(-resume)
run_tool "${command[@]}"
step_success "Nextflow orchestration completed"
