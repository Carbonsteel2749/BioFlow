#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/../lib/common.sh"
bins=""; assembly=""; reads_list=""; outdir=""; threads=8; sample_manifest=""; database_manifest=""; task_id=manual; state_root=""
while (($#)); do case "$1" in
 --bins) bins="$2"; shift 2;; --assembly) assembly="$2"; shift 2;; --reads-list) reads_list="$2"; shift 2;; --outdir) outdir="$2"; shift 2;; --sample-manifest) sample_manifest="$2"; shift 2;; --database-manifest) database_manifest="$2"; shift 2;; --threads) threads="$2"; shift 2;; --task-id) task_id="$2"; shift 2;; --state-root) state_root="$2"; shift 2;; --resume) PIPELINE_RESUME=1; shift;; *) echo "unknown argument: $1" >&2; exit 64;; esac; done
[[ -n "$outdir" && -n "$sample_manifest" && -n "$database_manifest" ]] || { echo "--outdir, --sample-manifest and --database-manifest are required" >&2; exit 64; }; state_root="${state_root:-$(dirname "$outdir")}"; mkdir -p "$outdir"
export PIPELINE_TASK_ID="$task_id" PIPELINE_SAMPLE_ID=global PIPELINE_LOG_DIR="$state_root/logs" PIPELINE_STATUS_DIR="$state_root/status"
step_prepare bin_quantification; trap 'step_fail "$?" "$LINENO" "${CURRENT_COMMAND:-$BASH_COMMAND}"' ERR
abundance="$outdir/bin_abundance_table.tab"; normalized="$outdir/sample_mag_abundance.tsv"; provenance="$outdir/sample_mag_abundance.provenance.json"; if checkpoint_valid "$abundance" "$normalized" "$provenance"; then exit 0; fi
step_start "bin abundance quantification started"; require_command metawrap; require_dir "$bins"; require_file "$assembly"; require_file "$reads_list"; require_file "$sample_manifest"; require_file "$database_manifest"
tmpdir="$(mktemp -d "$outdir/.quant-input.XXXXXX")"; cleanup() { rm -rf "$tmpdir"; }; trap cleanup EXIT
reads=(); index=0; while IFS= read -r input; do [[ -n "$input" ]] || continue; index=$((index+1)); target="$tmpdir/read_${index}.fastq"; if [[ "$input" == *.gz ]]; then gzip -cd -- "$input" > "$target"; else cp -- "$input" "$target"; fi; reads+=("$target"); done < "$reads_list"
run_tool metawrap quant_bins -b "$bins" -o "$outdir" -a "$assembly" -t "$threads" "${reads[@]}"
python3 "$SCRIPT_DIR/normalize_mag_abundance.py" --source "$abundance" --sample-manifest "$sample_manifest" --database-manifest "$database_manifest" --output "$normalized" --task-id "$task_id"
step_success "bin abundance quantification completed" "$abundance" "$normalized" "$provenance"
