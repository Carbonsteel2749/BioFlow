#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/../lib/common.sh"
sample=""; r1=""; r2=""; database_manifest=""; outdir=""; threads=4; read_length=150; task_id=manual; state_root=""
while (($#)); do case "$1" in
 --sample) sample="$2"; shift 2;; --r1) r1="$2"; shift 2;; --r2) r2="$2"; shift 2;; --database-manifest) database_manifest="$2"; shift 2;;
 --outdir) outdir="$2"; shift 2;; --threads) threads="$2"; shift 2;; --read-length) read_length="$2"; shift 2;;
 --task-id) task_id="$2"; shift 2;; --state-root) state_root="$2"; shift 2;; --resume) PIPELINE_RESUME=1; shift;;
 *) echo "unknown argument: $1" >&2; exit 64;; esac; done
[[ -n "$sample" && -n "$outdir" && -n "$database_manifest" ]] || { echo "--sample, --outdir and --database-manifest are required" >&2; exit 64; }
state_root="${state_root:-$(dirname "$outdir")}"; mkdir -p "$outdir"
export PIPELINE_TASK_ID="$task_id" PIPELINE_SAMPLE_ID="$sample" PIPELINE_LOG_DIR="$state_root/logs" PIPELINE_STATUS_DIR="$state_root/status"
step_prepare taxonomy; trap 'step_fail "$?" "$LINENO" "${CURRENT_COMMAND:-$BASH_COMMAND}"' ERR
kraken_report="$outdir/kraken.report"; kraken_output="$outdir/kraken.output"; bracken_output="$outdir/bracken.species.tsv"; abundance_output="$outdir/species_abundance.tsv"; validation="$outdir/taxonomy.validation.json"; provenance="$outdir/taxonomy.provenance.json"
if checkpoint_valid "$kraken_report" "$kraken_output" "$bracken_output" "$abundance_output" "$validation" "$provenance"; then exit 0; fi
require_file "$database_manifest"; require_file "$r1"; require_file "$r2"; require_command kraken2; require_command bracken
mapfile -t db_values < <(python3 - "$database_manifest" <<'PY'
import json, sys
data=json.load(open(sys.argv[1], encoding="utf-8"))
entry=data.get("databases", {}).get("taxonomy_reads", {}).get("kraken2")
bracken=data.get("databases", {}).get("taxonomy_reads", {}).get("bracken")
if not isinstance(entry, dict) or not isinstance(bracken, dict):
    raise SystemExit("database manifest lacks taxonomy_reads.kraken2/bracken")
for key in ("path","release","taxonomy_system","manifest_sha256"):
    if not entry.get(key) or entry.get(key) != bracken.get(key):
        raise SystemExit(f"inconsistent Kraken2/Bracken database manifest field: {key}")
profile=data.get("database_profile")
if not isinstance(profile,str) or not profile.strip(): raise SystemExit("database manifest has no database_profile")
print(entry["path"]); print(entry["release"]); print(entry["taxonomy_system"]); print(entry["manifest_sha256"]); print(profile)
for sentinel in entry.get("required_sentinel_files", []): print("SENTINEL="+sentinel)
PY
)
kraken_db="${db_values[0]}"; database_release="${db_values[1]}"; taxonomy_system="${db_values[2]}"; database_sha="${db_values[3]}"; database_profile="${db_values[4]}"
require_dir "$kraken_db"
for value in "${db_values[@]:4}"; do [[ "$value" == SENTINEL=* ]] || continue; require_file "$kraken_db/${value#SENTINEL=}"; done
step_start "taxonomic profiling started"
run_tool kraken2 --db "$kraken_db" --threads "$threads" --paired --report "$kraken_report" --output "$kraken_output" "$r1" "$r2"
run_tool bracken -d "$kraken_db" -i "$kraken_report" -o "$bracken_output" -r "$read_length" -l S
python3 - "$bracken_output" "$abundance_output" "$sample" "$taxonomy_system" "$database_profile" "$database_release" <<'PY'
import csv, sys
source, destination, sample, system, profile, release = sys.argv[1:]
fields=["sample_id","taxid","taxonomy","taxonomy_rank","taxonomy_system","read_count_or_estimated_reads","abundance","abundance_unit","classification_source","database_profile","database_release"]
with open(destination,"w",newline="",encoding="utf-8") as out:
    writer=csv.DictWriter(out,fieldnames=fields,delimiter="\t"); writer.writeheader()
    try:
        rows=csv.DictReader(open(source,encoding="utf-8"),delimiter="\t")
        for row in rows:
            rank=row.get("taxonomy_lvl","").strip().lower()
            rank={"s":"species","species":"species"}.get(rank,rank)
            writer.writerow({"sample_id":sample,"taxid":row.get("taxonomy_id",""),"taxonomy":row.get("name",""),"taxonomy_rank":rank,"taxonomy_system":system,"read_count_or_estimated_reads":row.get("new_est_reads",row.get("kraken_assigned_reads","")),"abundance":row.get("fraction_total_reads",""),"abundance_unit":"fraction_of_reads","classification_source":"Kraken2+Bracken","database_profile":profile,"database_release":release})
    except csv.Error:
        pass
PY
python3 "$SCRIPT_DIR/validate_result_tables.py" taxonomy --table "$abundance_output" --kraken-report "$kraken_report" --output "$validation" --sample "$sample" --profile "$database_profile" --release "$database_release" --system "$taxonomy_system"
kraken_version="$(kraken2 --version 2>&1 | head -n 1 || true)"; bracken_version="$(bracken -v 2>&1 | head -n 1 || true)"; workflow_revision="$(git -C "$SCRIPT_DIR/../.." rev-parse HEAD 2>/dev/null || echo unavailable)"
python3 - "$database_manifest" "$provenance" "$sample" "$r1" "$r2" "$task_id" "$workflow_revision" "$kraken_version" "$bracken_version" "$kraken_report" "$kraken_output" "$bracken_output" "$abundance_output" "$read_length" <<'PY'
import json,sys
manifest,out,sample,r1,r2,task,revision,kraken,bracken,*rest=sys.argv[1:]
data=json.load(open(manifest,encoding="utf-8")); db=data["databases"]["taxonomy_reads"]["kraken2"]
json.dump({"workflow_revision":revision,"task_or_run_id":task,"sample_id_or_mag_id":sample,"tool_name":"Kraken2+Bracken","tool_versions":{"kraken2":kraken,"bracken":bracken},"command_or_sanitized_parameters":{"read_length":rest[-1]},"database_profile":data["database_profile"],"database_name":db["database_name"],"database_release":db["release"],"taxonomy_system":db["taxonomy_system"],"database_manifest_sha256":data["resolved_manifest_sha256"],"input_files":[r1,r2],"output_files":rest[:-1]},open(out,"w",encoding="utf-8"),indent=2)
PY
step_success "taxonomic profiling completed" "$kraken_report" "$kraken_output" "$bracken_output" "$abundance_output" "$validation" "$provenance"
