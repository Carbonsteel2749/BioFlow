#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/../lib/common.sh"
cohort_id=""; bins=""; outdir=""; threads=8; database_manifest=""; task_id=manual; state_root=""
while (($#)); do case "$1" in
 --cohort-id) cohort_id="$2"; shift 2;; --bins) bins="$2"; shift 2;; --outdir) outdir="$2"; shift 2;; --threads) threads="$2"; shift 2;; --database-manifest) database_manifest="$2"; shift 2;; --task-id) task_id="$2"; shift 2;; --state-root) state_root="$2"; shift 2;; --resume) PIPELINE_RESUME=1; shift;; *) echo "unknown argument: $1" >&2; exit 64;; esac; done
[[ -n "$cohort_id" && -n "$outdir" && -n "$database_manifest" ]] || { echo "--cohort-id, --outdir and --database-manifest are required" >&2; exit 64; }; state_root="${state_root:-$(dirname "$outdir")}"; mkdir -p "$outdir/classification" "$outdir/function"
export PIPELINE_TASK_ID="$task_id" PIPELINE_SAMPLE_ID="$cohort_id" PIPELINE_LOG_DIR="$state_root/logs" PIPELINE_STATUS_DIR="$state_root/status"
step_prepare bin_annotation; trap 'step_fail "$?" "$LINENO" "${CURRENT_COMMAND:-$BASH_COMMAND}"' ERR
taxonomy="$outdir/classification/bin_taxonomy.tab"; functions="$outdir/function/bin_funct_annotations"; normalized="$outdir/mag_annotation.tsv"; mag_functions="$outdir/mag_function_annotation.tsv"; provenance="$outdir/mag_annotation.provenance.json"
if checkpoint_valid "$taxonomy" "$functions" "$normalized" "$mag_functions" "$provenance"; then exit 0; fi
step_start "bin taxonomic and functional annotation started cohort=$cohort_id"; require_command metawrap; require_dir "$bins"; require_file "$database_manifest"
if ! mag_database_text="$(python3 - "$database_manifest" <<'PY'
import json, sys
from pathlib import Path
data=json.load(open(sys.argv[1],encoding="utf-8")); profile=data.get("database_profile"); entries=data.get("databases",{}).get("mag_annotation",{})
if not isinstance(profile,str) or not profile.strip(): raise SystemExit("database manifest lacks database_profile")
for name in ("classification","function"):
    entry=entries.get(name)
    if not isinstance(entry,dict) or not isinstance(entry.get("path"),str) or not entry["path"].strip(): raise SystemExit(f"database manifest lacks mag_annotation.{name}.path")
    path=Path(entry["path"])
    if not path.is_dir(): raise SystemExit(f"MAG database is unavailable: {name}")
    for sentinel in entry.get("required_sentinel_files",[]):
        if not (path/sentinel).is_file(): raise SystemExit(f"MAG database sentinel is unavailable: {name}/{sentinel}")
    print(path)
PY
)"; then
  log_message ERROR "invalid_database_manifest scope=mag_annotation reason=database_profile_or_path_missing"
  exit 65
fi
mapfile -t mag_database_paths <<< "$mag_database_text"
(( ${#mag_database_paths[@]} == 2 )) || { log_message ERROR "invalid_database_manifest scope=mag_annotation reason=unexpected_database_path_count"; exit 65; }
run_tool metawrap classify_bins -b "$bins" -o "$outdir/classification" -t "$threads"
run_tool metawrap annotate_bins -b "$bins" -o "$outdir/function" -t "$threads"
find "$functions" -type f -size +0c -print -quit | grep -q . || { log_message ERROR "bin annotation produced no files"; exit 65; }
quality_file="$(find "$(dirname "$bins")" -maxdepth 1 -name '*.stats' -type f -print -quit || true)"; quality_args=(); [[ -n "$quality_file" ]] && quality_args=(--quality "$quality_file")
python3 "$SCRIPT_DIR/normalize_mag_annotation.py" --cohort-id "$cohort_id" --bins "$bins" --taxonomy "$taxonomy" --functions "$functions" --database-manifest "$database_manifest" --outdir "$outdir" --task-id "$task_id" "${quality_args[@]}"
step_success "bin taxonomic and functional annotation completed" "$taxonomy" "$functions" "$normalized" "$mag_functions" "$provenance"
