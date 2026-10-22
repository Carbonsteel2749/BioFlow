#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; source "$SCRIPT_DIR/../lib/common.sh"
sample=""; r1=""; r2=""; outdir=""; threads=4; database_manifest=""; task_id=manual; state_root=""
while (($#)); do case "$1" in
 --sample) sample="$2"; shift 2;; --r1) r1="$2"; shift 2;; --r2) r2="$2"; shift 2;; --outdir) outdir="$2"; shift 2;;
 --threads) threads="$2"; shift 2;; --database-manifest) database_manifest="$2"; shift 2;;
 --task-id) task_id="$2"; shift 2;; --state-root) state_root="$2"; shift 2;; --resume) PIPELINE_RESUME=1; shift;;
 *) echo "unknown argument: $1" >&2; exit 64;; esac; done
[[ -n "$sample" && -n "$outdir" && -n "$database_manifest" ]] || { echo "--sample, --outdir and --database-manifest are required" >&2; exit 64; }
state_root="${state_root:-$(dirname "$outdir")}"; mkdir -p "$outdir"
export PIPELINE_TASK_ID="$task_id" PIPELINE_SAMPLE_ID="$sample" PIPELINE_LOG_DIR="$state_root/logs" PIPELINE_STATUS_DIR="$state_root/status"
step_prepare functional_annotation; trap 'step_fail "$?" "$LINENO" "${CURRENT_COMMAND:-$BASH_COMMAND}"' ERR
families="$outdir/read_genefamilies.tsv"; pathabundance="$outdir/read_pathabundance.tsv"; pathways="$outdir/read_pathways.tsv"; pathcoverage="$outdir/read_pathcoverage.tsv"; ko="$outdir/read_ko.tsv"; ec="$outdir/read_ec.tsv"; provenance="$outdir/functional.provenance.json"
raw_families="$outdir/humann_raw_genefamilies.tsv"; raw_pathabundance="$outdir/humann_raw_pathabundance.tsv"; raw_pathcoverage="$outdir/humann_raw_pathcoverage.tsv"
raw_ko="$outdir/humann_raw_ko.tsv"; raw_ec="$outdir/humann_raw_ec.tsv"
function_validation="$outdir/functional.validation.json"
if checkpoint_valid "$families" "$pathabundance" "$pathways" "$pathcoverage" "$ko" "$ec" "$function_validation" "$provenance" "$raw_families" "$raw_pathabundance" "$raw_pathcoverage" "$raw_ko" "$raw_ec"; then exit 0; fi
require_file "$database_manifest"; require_file "$r1"; require_file "$r2"; require_command humann
if ! database_paths_text="$(python3 - "$database_manifest" <<'PY'
import json,sys
from pathlib import Path

data=json.load(open(sys.argv[1],encoding="utf-8")); entries=data.get("databases",{}).get("function_reads",{})
for name in ("humann_nucleotide","humann_protein","humann_utility","metaphlan"):
    entry=entries.get(name)
    if not isinstance(entry,dict) or not entry.get("path"): raise SystemExit(f"database manifest lacks function_reads.{name}")
    print(entry["path"])

for name in ("ko_mapping", "ec_mapping"):
    entry=entries.get(name)
    if not isinstance(entry,dict) or not entry.get("path"):
        raise SystemExit(f"database manifest lacks function_reads.{name}")
    sentinels=entry.get("required_sentinel_files")
    if not isinstance(sentinels,list) or len(sentinels) != 1 or not isinstance(sentinels[0],str) or not sentinels[0]:
        raise SystemExit(f"database manifest function_reads.{name} must define exactly one required_sentinel_files entry")
    print(Path(entry["path"]) / sentinels[0])
PY
)"; then
  log_message ERROR "invalid_database_manifest scope=function_reads reason=missing_or_invalid_mapping_entry"
  exit 65
fi
mapfile -t database_paths <<< "$database_paths_text"
if (( ${#database_paths[@]} != 6 )); then
  log_message ERROR "invalid_database_manifest scope=function_reads reason=unexpected_database_path_count"
  exit 65
fi
nucleotide_db="${database_paths[0]}"; protein_db="${database_paths[1]}"; utility_db="${database_paths[2]}"; metaphlan_db="${database_paths[3]}"
ko_mapping_file="${database_paths[4]}"; ec_mapping_file="${database_paths[5]}"
require_dir "$nucleotide_db"; require_dir "$protein_db"; require_dir "$utility_db"; require_dir "$metaphlan_db"
require_file "$ko_mapping_file"; require_file "$ec_mapping_file"
regroup_tool="$(command -v humann_regroup_table 2>/dev/null || true)"
if [[ -z "$regroup_tool" ]]; then
  regroup_tool="$(python3 - "$utility_db" <<'PY'
import os
import sys
from pathlib import Path

utility = Path(sys.argv[1]).resolve()
for parent in (utility, *utility.parents):
    candidate = parent / "bin" / "humann_regroup_table"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        print(candidate)
        break
PY
)"
fi
if [[ -z "$regroup_tool" || ! -x "$regroup_tool" ]]; then
  log_message ERROR "missing_dependency command=humann_regroup_table utility_db=$utility_db"
  return 127
fi
normalize_table() {
  local source="$1" destination="$2" function_type="$3" namespace="$4" release_db="$5"
  python3 - "$database_manifest" "$sample" "$source" "$destination" "$function_type" "$namespace" "$release_db" <<'PY'
import csv,json,sys
manifest,sample,source,destination,function_type,namespace,db_key=sys.argv[1:]; data=json.load(open(manifest)); db=data["databases"]["function_reads"][db_key]
headers=["sample_id","function_source","function_namespace","function_id","function_name","function_type","abundance","abundance_unit","relative_abundance","normalization_method","evidence_level","database_profile","database_release"]
headers.insert(-2,"analysis_scope")
with open(destination,"w",newline="",encoding="utf-8") as out:
 w=csv.DictWriter(out,fieldnames=headers,delimiter="\t"); w.writeheader()
 for row in csv.reader(open(source,encoding="utf-8"),delimiter="\t"):
  if not row or row[0].startswith("#") or row[0].strip().lower() in ("feature","gene family","pathway"): continue
  if "|" in row[0]: continue
  w.writerow({"sample_id":sample,"function_source":"HUMAnN","function_namespace":namespace,"function_id":row[0],"function_name":row[0],"function_type":function_type,"abundance":row[1] if len(row)>1 else "","abundance_unit":"HUMAnN_reported","relative_abundance":"","normalization_method":"HUMAnN raw output","evidence_level":"reads","analysis_scope":"community_total","database_profile":data["database_profile"],"database_release":db["release"]})
PY
}
step_start "functional profiling started"
merged="$(mktemp "$outdir/${sample}.merged.XXXXXX.fastq")"; cleanup() { rm -f "$merged"; }; trap cleanup EXIT
if [[ "$r1" == *.gz ]]; then run_tool bash -c 'gzip -cd -- "$1" "$2" > "$3"' _ "$r1" "$r2" "$merged"; else run_tool bash -c 'cat -- "$1" "$2" > "$3"' _ "$r1" "$r2" "$merged"; fi
run_tool humann --input "$merged" --output "$outdir" --output-basename "$sample" --threads "$threads" --nucleotide-database "$nucleotide_db" --protein-database "$protein_db" --metaphlan-options "--bowtie2db $metaphlan_db --offline"
humann_families="$outdir/${sample}_genefamilies.tsv"; humann_pathabundance="$outdir/${sample}_pathabundance.tsv"; humann_pathcoverage="$outdir/${sample}_pathcoverage.tsv"
require_file "$humann_families"; require_file "$humann_pathabundance"; require_file "$humann_pathcoverage"
cp "$humann_families" "$raw_families"; cp "$humann_pathabundance" "$raw_pathabundance"; cp "$humann_pathcoverage" "$raw_pathcoverage"
run_tool "$regroup_tool" --input "$humann_families" --custom "$ko_mapping_file" --output "$raw_ko"
run_tool "$regroup_tool" --input "$humann_families" --custom "$ec_mapping_file" --output "$raw_ec"
normalize_table "$humann_families" "$families" gene_family "UniRef90" humann_protein
normalize_table "$humann_pathabundance" "$pathabundance" pathway_abundance MetaCyc humann_nucleotide
normalize_table "$humann_pathcoverage" "$pathcoverage" pathway_coverage MetaCyc humann_nucleotide
normalize_table "$raw_ko" "$ko" ko KO ko_mapping
normalize_table "$raw_ec" "$ec" ec EC ec_mapping
cp "$pathabundance" "$pathways"
database_profile="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1],encoding="utf-8"))["database_profile"])' "$database_manifest")"
validation_tmp="$outdir/.functional-validation"
mkdir -p "$validation_tmp"
python3 "$SCRIPT_DIR/validate_result_tables.py" functional --table "$families" --output "$validation_tmp/gene.json" --sample "$sample" --profile "$database_profile" --release "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["databases"]["function_reads"]["humann_protein"]["release"])' "$database_manifest")" --namespace UniRef90 --kind gene_family
python3 "$SCRIPT_DIR/validate_result_tables.py" functional --table "$pathabundance" --output "$validation_tmp/pathway.json" --sample "$sample" --profile "$database_profile" --release "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["databases"]["function_reads"]["humann_nucleotide"]["release"])' "$database_manifest")" --namespace MetaCyc --kind pathway_abundance
python3 "$SCRIPT_DIR/validate_result_tables.py" functional --table "$pathcoverage" --output "$validation_tmp/coverage.json" --sample "$sample" --profile "$database_profile" --release "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["databases"]["function_reads"]["humann_nucleotide"]["release"])' "$database_manifest")" --namespace MetaCyc --kind pathway_coverage
python3 "$SCRIPT_DIR/validate_result_tables.py" functional --table "$ko" --output "$validation_tmp/ko.json" --sample "$sample" --profile "$database_profile" --release "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["databases"]["function_reads"]["ko_mapping"]["release"])' "$database_manifest")" --namespace KO --kind ko
python3 "$SCRIPT_DIR/validate_result_tables.py" functional --table "$ec" --output "$validation_tmp/ec.json" --sample "$sample" --profile "$database_profile" --release "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["databases"]["function_reads"]["ec_mapping"]["release"])' "$database_manifest")" --namespace EC --kind ec
python3 - "$function_validation" "$validation_tmp" <<'PY'
import json,sys
out,root=sys.argv[1:]; records=[json.load(open(f"{root}/{name}")) for name in ("gene.json","pathway.json","coverage.json","ko.json","ec.json")]
json.dump({"analysis":"functional","status":"no_pathway_results" if records[1]["status"]=="no_unstratified_results" else "valid","tables":records},open(out,"w"),indent=2)
PY
humann_version="$(humann --version 2>&1 | head -n 1 || true)"; regroup_version="${humann_version:-unavailable} (bundled humann_regroup_table)"; workflow_revision="$(git -C "$SCRIPT_DIR/../.." rev-parse HEAD 2>/dev/null || echo unavailable)"
python3 - "$database_manifest" "$provenance" "$sample" "$r1" "$r2" "$task_id" "$workflow_revision" "$humann_version" "$regroup_version" "$families" "$pathabundance" "$pathcoverage" "$ko" "$ec" "$raw_families" "$raw_pathabundance" "$raw_pathcoverage" "$raw_ko" "$raw_ec" <<'PY'
import json,sys
manifest,out,sample,r1,r2,task,revision,humann,regroup,*outputs=sys.argv[1:]; data=json.load(open(manifest)); function_dbs=data["databases"]["function_reads"]; db=function_dbs["humann_protein"]
json.dump({"workflow_revision":revision,"task_or_run_id":task,"sample_id_or_mag_id":sample,"tool_name":"HUMAnN","tool_versions":{"humann":humann,"humann_regroup_table":regroup},"command_or_sanitized_parameters":{"threads":"recorded in step log","global_database_fallback":False},"database_profile":data["database_profile"],"database_name":db["database_name"],"database_release":db["release"],"database_releases":{name: entry["release"] for name,entry in sorted(function_dbs.items())},"taxonomy_system":function_dbs["metaphlan"]["taxonomy_system"],"database_manifest_sha256":data["resolved_manifest_sha256"],"input_files":[r1,r2],"output_files":outputs},open(out,"w",encoding="utf-8"),indent=2)
PY
step_success "functional profiling completed" "$families" "$pathabundance" "$pathways" "$pathcoverage" "$ko" "$ec" "$function_validation" "$raw_families" "$raw_pathabundance" "$raw_pathcoverage" "$raw_ko" "$raw_ec" "$provenance"
