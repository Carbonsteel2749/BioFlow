#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/../lib/common.sh"
bins_a=""; bins_b=""; bins_c=""; outdir=""; threads=8; completeness=70; contamination=5; task_id=manual; state_root=""
while (($#)); do case "$1" in
 --bins-a) bins_a="$2"; shift 2;; --bins-b) bins_b="$2"; shift 2;; --bins-c) bins_c="$2"; shift 2;; --outdir) outdir="$2"; shift 2;;
 --threads) threads="$2"; shift 2;; --completeness) completeness="$2"; shift 2;; --contamination) contamination="$2"; shift 2;;
 --task-id) task_id="$2"; shift 2;; --state-root) state_root="$2"; shift 2;; --resume) PIPELINE_RESUME=1; shift;;
 *) echo "unknown argument: $1" >&2; exit 64;; esac; done
[[ -n "$outdir" ]] || { echo "--outdir is required" >&2; exit 64; }; state_root="${state_root:-$(dirname "$outdir")}"; mkdir -p "$outdir"
export PIPELINE_TASK_ID="$task_id" PIPELINE_SAMPLE_ID=global PIPELINE_LOG_DIR="$state_root/logs" PIPELINE_STATUS_DIR="$state_root/status"
step_prepare bin_refinement; trap 'step_fail "$?" "$LINENO" "${CURRENT_COMMAND:-$BASH_COMMAND}"' ERR
final_bins="$outdir/metawrap_${completeness}_${contamination}_bins"; stats="$outdir/metawrap_${completeness}_${contamination}_bins.stats"
if checkpoint_valid "$final_bins" "$stats"; then exit 0; fi
step_start "bin refinement started completeness=$completeness contamination=$contamination"; require_command metawrap; require_dir "$bins_a"; require_dir "$bins_b"; require_dir "$bins_c"
run_tool metawrap bin_refinement -o "$outdir" -t "$threads" -A "$bins_a" -B "$bins_b" -C "$bins_c" -c "$completeness" -x "$contamination"
find "$final_bins" -type f -size +0c -print -quit | grep -q . || { log_message ERROR "bin refinement produced no bins"; exit 65; }
step_success "bin refinement completed" "$final_bins" "$stats"
