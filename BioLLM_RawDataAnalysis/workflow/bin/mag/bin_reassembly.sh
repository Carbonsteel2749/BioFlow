#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/../lib/common.sh"
bins=""; r1_list=""; r2_list=""; outdir=""; threads=8; memory_gb=32; completeness=70; contamination=5; task_id=manual; state_root=""
while (($#)); do case "$1" in
 --bins) bins="$2"; shift 2;; --r1-list) r1_list="$2"; shift 2;; --r2-list) r2_list="$2"; shift 2;; --outdir) outdir="$2"; shift 2;;
 --threads) threads="$2"; shift 2;; --memory-gb) memory_gb="$2"; shift 2;; --completeness) completeness="$2"; shift 2;; --contamination) contamination="$2"; shift 2;;
 --task-id) task_id="$2"; shift 2;; --state-root) state_root="$2"; shift 2;; --resume) PIPELINE_RESUME=1; shift;;
 *) echo "unknown argument: $1" >&2; exit 64;; esac; done
[[ -n "$outdir" ]] || { echo "--outdir is required" >&2; exit 64; }; state_root="${state_root:-$(dirname "$outdir")}"; mkdir -p "$outdir"
export PIPELINE_TASK_ID="$task_id" PIPELINE_SAMPLE_ID=global PIPELINE_LOG_DIR="$state_root/logs" PIPELINE_STATUS_DIR="$state_root/status"
step_prepare bin_reassembly; trap 'step_fail "$?" "$LINENO" "${CURRENT_COMMAND:-$BASH_COMMAND}"' ERR
reassembled="$outdir/reassembled_bins"; stats="$outdir/reassembled_bins.stats"; if checkpoint_valid "$reassembled" "$stats"; then exit 0; fi
step_start "bin reassembly started"; require_command metawrap; require_dir "$bins"; require_file "$r1_list"; require_file "$r2_list"
tmpdir="$(mktemp -d "$outdir/.reassembly-input.XXXXXX")"; cleanup() { rm -rf "$tmpdir"; }; trap cleanup EXIT
concat_list() { local list="$1" destination="$2" input; : > "$destination"; while IFS= read -r input; do [[ -n "$input" ]] || continue; if [[ "$input" == *.gz ]]; then gzip -cd -- "$input" >> "$destination"; else cat -- "$input" >> "$destination"; fi; done < "$list"; }
concat_list "$r1_list" "$tmpdir/all_R1.fastq"; concat_list "$r2_list" "$tmpdir/all_R2.fastq"
run_tool metawrap reassemble_bins -o "$outdir" -1 "$tmpdir/all_R1.fastq" -2 "$tmpdir/all_R2.fastq" -t "$threads" -m "$memory_gb" -c "$completeness" -x "$contamination" -b "$bins"
step_success "bin reassembly completed" "$reassembled" "$stats"
