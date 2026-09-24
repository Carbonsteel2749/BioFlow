#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/../lib/common.sh"
r1_list=""; r2_list=""; outdir=""; threads=8; memory_gb=32; assembler=megahit; task_id=manual; state_root=""
while (($#)); do case "$1" in
 --r1-list) r1_list="$2"; shift 2;; --r2-list) r2_list="$2"; shift 2;; --outdir) outdir="$2"; shift 2;;
 --threads) threads="$2"; shift 2;; --memory-gb) memory_gb="$2"; shift 2;; --assembler) assembler="$2"; shift 2;;
 --task-id) task_id="$2"; shift 2;; --state-root) state_root="$2"; shift 2;; --resume) PIPELINE_RESUME=1; shift;;
 *) echo "unknown argument: $1" >&2; exit 64;; esac; done
[[ -n "$outdir" ]] || { echo "--outdir is required" >&2; exit 64; }; state_root="${state_root:-$(dirname "$outdir")}"; mkdir -p "$outdir"
export PIPELINE_TASK_ID="$task_id" PIPELINE_SAMPLE_ID=global PIPELINE_LOG_DIR="$state_root/logs" PIPELINE_STATUS_DIR="$state_root/status"
step_prepare assembly; trap 'step_fail "$?" "$LINENO" "${CURRENT_COMMAND:-$BASH_COMMAND}"' ERR
assembly="$outdir/final_assembly.fasta"; report="$outdir/assembly_report.html"
if checkpoint_valid "$assembly" "$report"; then exit 0; fi
step_start "co-assembly started assembler=$assembler"; require_command metawrap; require_file "$r1_list"; require_file "$r2_list"
tmpdir="$(mktemp -d "$outdir/.assembly-input.XXXXXX")"; cleanup() { rm -rf "$tmpdir"; }; trap cleanup EXIT
concat_list() { local list="$1" destination="$2" input; : > "$destination"; while IFS= read -r input; do [[ -n "$input" ]] || continue; require_file "$input"; if [[ "$input" == *.gz ]]; then gzip -cd -- "$input" >> "$destination"; else cat -- "$input" >> "$destination"; fi; done < "$list"; }
concat_list "$r1_list" "$tmpdir/coassembly_R1.fastq"; concat_list "$r2_list" "$tmpdir/coassembly_R2.fastq"
command=(metawrap assembly -1 "$tmpdir/coassembly_R1.fastq" -2 "$tmpdir/coassembly_R2.fastq" -m "$memory_gb" -t "$threads" -o "$outdir")
[[ "$assembler" == metaspades ]] && command+=(--use-metaspades)
[[ "$assembler" == megahit || "$assembler" == metaspades ]] || { log_message ERROR "unsupported assembler=$assembler"; exit 64; }
run_tool "${command[@]}"; step_success "co-assembly completed" "$assembly" "$report"
