#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/../lib/common.sh"
assembly=""; reads_list=""; outdir=""; threads=8; task_id=manual; state_root=""
while (($#)); do case "$1" in
 --assembly) assembly="$2"; shift 2;; --reads-list) reads_list="$2"; shift 2;; --outdir) outdir="$2"; shift 2;;
 --threads) threads="$2"; shift 2;; --task-id) task_id="$2"; shift 2;; --state-root) state_root="$2"; shift 2;;
 --resume) PIPELINE_RESUME=1; shift;; *) echo "unknown argument: $1" >&2; exit 64;; esac; done
[[ -n "$outdir" ]] || { echo "--outdir is required" >&2; exit 64; }; state_root="${state_root:-$(dirname "$outdir")}"; mkdir -p "$outdir"
export PIPELINE_TASK_ID="$task_id" PIPELINE_SAMPLE_ID=global PIPELINE_LOG_DIR="$state_root/logs" PIPELINE_STATUS_DIR="$state_root/status"
step_prepare binning; trap 'step_fail "$?" "$LINENO" "${CURRENT_COMMAND:-$BASH_COMMAND}"' ERR
outputs=("$outdir/metabat2_bins" "$outdir/maxbin2_bins" "$outdir/concoct_bins")
if checkpoint_valid "${outputs[@]}"; then exit 0; fi
step_start "three-algorithm binning started"; require_command metawrap; require_file "$assembly"; require_file "$reads_list"
tmpdir="$(mktemp -d "$outdir/.binning-input.XXXXXX")"; cleanup() { rm -rf "$tmpdir"; }; trap cleanup EXIT
reads=(); index=0; while IFS= read -r input; do [[ -n "$input" ]] || continue; require_file "$input"; index=$((index+1)); target="$tmpdir/read_${index}.fastq"; if [[ "$input" == *.gz ]]; then gzip -cd -- "$input" > "$target"; else cp -- "$input" "$target"; fi; reads+=("$target"); done < "$reads_list"
((${#reads[@]} >= 2)) || { log_message ERROR "reads list must contain at least one pair"; exit 66; }
run_tool metawrap binning -o "$outdir" -t "$threads" -a "$assembly" --metabat2 --maxbin2 --concoct "${reads[@]}"
for directory in "${outputs[@]}"; do find "$directory" -type f -size +0c -print -quit | grep -q . || { log_message ERROR "no bins produced in $directory"; exit 65; }; done
step_success "three-algorithm binning completed" "${outputs[@]}"
