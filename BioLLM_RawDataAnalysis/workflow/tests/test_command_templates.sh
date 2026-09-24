#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
MOCK_BIN="$TMP_ROOT/mock-bin"
mkdir -p "$MOCK_BIN"
export PATH="$MOCK_BIN:$PATH"

write_mock() {
  local name="$1"
  shift
  printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' "$*" > "$MOCK_BIN/$name"
  chmod +x "$MOCK_BIN/$name"
}

assert_status() {
  local path="$1"
  local expected="$2"
  python3 - "$path" "$expected" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
if payload["status"] != sys.argv[2]:
    raise SystemExit(f"unexpected status: {payload}")
PY
}

write_mock fastqc '
outdir=""
inputs=()
while (($#)); do
  case "$1" in
    --outdir) outdir="$2"; shift 2 ;;
    --threads) shift 2 ;;
    *) inputs+=("$1"); shift ;;
  esac
done
mkdir -p "$outdir"
for input in "${inputs[@]}"; do
  base="$(basename "$input")"
  base="${base%.gz}"; base="${base%.fastq}"; base="${base%.fq}"
  printf html > "$outdir/${base}_fastqc.html"
  python3 - "$outdir/${base}_fastqc.zip" "$base" <<PY
import sys
import zipfile
with zipfile.ZipFile(sys.argv[1], "w") as archive:
    archive.writestr(f"{sys.argv[2]}_fastqc/summary.txt", "PASS\\tBasic Statistics\\n")
    archive.writestr(f"{sys.argv[2]}_fastqc/fastqc_data.txt", ">>Basic Statistics\\tpass\\n")
PY
done'

write_mock fastp '
while (($#)); do
  case "$1" in
    --out1) printf "@read001/1\\nACGT\\n+\\n!!!!\\n" | gzip -c > "$2"; shift 2 ;;
    --out2) printf "@read001/2\\nTGCA\\n+\\n####\\n" | gzip -c > "$2"; shift 2 ;;
    --json) printf "{\\\"summary\\\":{\\\"before_filtering\\\":{\\\"total_reads\\\":2},\\\"after_filtering\\\":{\\\"total_reads\\\":2}}}" > "$2"; shift 2 ;;
    --html) printf result > "$2"; shift 2 ;;
    *) shift ;;
  esac
done'

write_mock bowtie2 '
pattern=""
while (($#)); do
  case "$1" in
    --un-conc-gz) pattern="$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [[ -n "$pattern" ]]; then
  printf "@read001/1\\nACGT\\n+\\n!!!!\\n" | gzip -c > "${pattern//%/1}"
  printf "@read001/2\\nTGCA\\n+\\n####\\n" | gzip -c > "${pattern//%/2}"
else
  printf "@HD\\tVN:1.6\\n"
fi
echo "50.00% overall alignment rate" >&2'

write_mock samtools '
[[ "${1:-}" == "--version" ]] && { echo "samtools mock"; exit 0; }
subcommand="$1"; shift
case "$subcommand" in
  view) cat ;;
  fastq)
    r1=""; r2=""
    while (($#)); do
      case "$1" in -1) r1="$2"; shift 2 ;; -2) r2="$2"; shift 2 ;; *) shift ;; esac
    done
    cat >/dev/null
    printf "@read001/1\\nACGT\\n+\\n!!!!\\n" > "$r1"
    printf "@read001/2\\nTGCA\\n+\\n####\\n" > "$r2"
    ;;
esac'

write_mock kraken2 '
while (($#)); do
  case "$1" in
    --report|--output) printf taxonomy > "$2"; shift 2 ;;
    *) shift ;;
  esac
done'

write_mock bracken '
while (($#)); do
  case "$1" in
    -o) printf abundance > "$2"; shift 2 ;;
    *) shift ;;
  esac
done'

write_mock humann '
if [[ "${1:-}" == "--version" ]]; then
  printf "humann v3.9\\n"
  exit 0
fi
printf "%s\n" "$@" > "${MOCK_HUMANN_ARGS:?}"
outdir=""; basename=""
while (($#)); do
  case "$1" in
    --output) outdir="$2"; shift 2 ;;
    --output-basename) basename="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$outdir"
printf "UniRef90_A0A000\\t1.0\\n" > "$outdir/${basename}_genefamilies.tsv"
printf "PWY-001\\t2.0\\n" > "$outdir/${basename}_pathabundance.tsv"
printf "PWY-001\\t0.5\\n" > "$outdir/${basename}_pathcoverage.tsv"'

write_mock multiqc '
outdir=""
while (($#)); do
  case "$1" in
    --outdir) outdir="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$outdir"
printf report > "$outdir/multiqc_report.html"'

write_mock nextflow '
printf "%s\n" "$@" > "${MOCK_NEXTFLOW_ARGS:?}"'

INPUT="$TMP_ROOT/input"
OUTPUT="$TMP_ROOT/output"
DATABASE_REGISTRY="$TMP_ROOT/database-registry.json"
DATABASE_MANIFEST="$TMP_ROOT/database.resolved.json"
mkdir -p "$INPUT" "$OUTPUT" "$TMP_ROOT/db/kraken" "$TMP_ROOT/db/host"
mkdir -p "$TMP_ROOT/db/humann/chocophlan" "$TMP_ROOT/db/humann/uniref" \
  "$TMP_ROOT/db/humann/utility" "$TMP_ROOT/db/humann/bin" "$TMP_ROOT/db/metaphlan" \
  "$TMP_ROOT/db/mag/classification" "$TMP_ROOT/db/mag/function"
export MOCK_REGROUP_LOG="$TMP_ROOT/humann-regroup.log"
cat > "$TMP_ROOT/db/humann/bin/humann_regroup_table" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--version" ]]; then
  printf 'version option is unsupported\n' >&2
  printf 'VERSION_CALLED\n' >> "${MOCK_REGROUP_LOG:?}"
  exit 2
fi
custom=""; output=""
while (($#)); do
  case "$1" in
    --input) shift 2 ;;
    --custom) custom="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    *) printf 'unexpected argument: %s\n' "$1" >&2; exit 64 ;;
  esac
done
[[ -f "$custom" && -n "$output" ]]
mapping="$(basename "$custom")"
printf '%s_feature\t1.0\n' "$mapping" > "$output"
printf '%s\n' "$mapping" >> "${MOCK_REGROUP_LOG:?}"
SH
chmod +x "$TMP_ROOT/db/humann/bin/humann_regroup_table"
for shard in 1 2 3 4 rev.1 rev.2; do : > "$TMP_ROOT/db/host/GRCh38.${shard}.bt2"; done
touch "$TMP_ROOT/db/kraken/hash.k2d" "$TMP_ROOT/db/kraken/opts.k2d" "$TMP_ROOT/db/kraken/taxo.k2d" \
  "$TMP_ROOT/db/humann/utility/map_ko_uniref90.txt.gz" \
  "$TMP_ROOT/db/humann/utility/map_level4ec_uniref90.txt.gz"
printf '@read001/1\nACGT\n+\n!!!!\n' > "$INPUT/S01_R1.fastq"
printf '@read001/2\nTGCA\n+\n!!!!\n' > "$INPUT/S01_R2.fastq"
printf 'sample_id,read1,read2\nS01,%s,%s\n' "$INPUT/S01_R1.fastq" "$INPUT/S01_R2.fastq" > "$INPUT/samples.csv"
python3 - "$DATABASE_REGISTRY" "$TMP_ROOT/db" <<'PY'
import json
import sys

output, root = sys.argv[1:]

def entry(name, purpose, path, taxonomy="not_applicable", sentinels=(), release="test-r1"):
    return {
        "database_name": name,
        "purpose": purpose,
        "path": f"{root}/{path}",
        "release": release,
        "taxonomy_system": taxonomy,
        "manifest_sha256": "test-manifest-sha256",
        "tool_compatibility_version": "test-v1",
        "required_sentinel_files": list(sentinels),
    }

kraken = entry(
    "Kraken2 test database",
    "read taxonomic classification",
    "kraken",
    "NCBI",
    ("hash.k2d", "opts.k2d", "taxo.k2d"),
)
profile = {
    "taxonomy_reads": {
        "kraken2": kraken,
        "bracken": dict(kraken),
    },
    "function_reads": {
        "humann_nucleotide": entry("ChocoPhlAn test", "nucleotide search", "humann/chocophlan"),
        "humann_protein": entry("UniRef test", "translated search", "humann/uniref"),
        "humann_utility": entry("HUMAnN utility test", "name mapping", "humann/utility"),
        "metaphlan": entry("MetaPhlAn test", "prescreen", "metaphlan", "MetaPhlAn SGB"),
        "ko_mapping": entry("UniRef90 to KO", "KO conversion", "humann/utility", sentinels=("map_ko_uniref90.txt.gz",), release="ko-r1"),
        "ec_mapping": entry("UniRef90 to EC", "EC conversion", "humann/utility", sentinels=("map_level4ec_uniref90.txt.gz",), release="ec-r1"),
    },
    "mag_annotation": {
        "classification": entry("MAG taxonomy test", "MAG classification", "mag/classification", "GTDB"),
        "function": entry("MAG function test", "MAG functional annotation", "mag/function"),
    },
}
json.dump({"schema_version": 1, "profiles": {"test": profile}}, open(output, "w"), indent=2)
PY
python3 "$PROJECT_ROOT/workflow/bin/core/validate_databases.py" \
  --registry "$DATABASE_REGISTRY" --profile test --output "$DATABASE_MANIFEST"

bash "$PROJECT_ROOT/workflow/bin/core/validate.sh" \
  --manifest "$INPUT/samples.csv" --outdir "$OUTPUT/validate" --task-id task-core
assert_status "$OUTPUT/status/global.validate.json" succeeded

bash "$PROJECT_ROOT/workflow/bin/core/fastqc.sh" \
  --sample S01 --r1 "$INPUT/S01_R1.fastq" --r2 "$INPUT/S01_R2.fastq" \
  --outdir "$OUTPUT/fastqc/S01" --threads 2 --task-id task-core --state-root "$OUTPUT"
assert_status "$OUTPUT/status/S01.fastqc_raw.json" succeeded

bash "$PROJECT_ROOT/workflow/bin/core/fastp.sh" \
  --sample S01 --r1 "$INPUT/S01_R1.fastq" --r2 "$INPUT/S01_R2.fastq" \
  --outdir "$OUTPUT/fastp/S01" --threads 2 --task-id task-core --state-root "$OUTPUT"
assert_status "$OUTPUT/status/S01.fastp.json" succeeded

bash "$PROJECT_ROOT/workflow/bin/core/host_depletion.sh" \
  --sample S01 --r1 "$OUTPUT/fastp/S01/S01.R1.clean.fastq.gz" \
  --r2 "$OUTPUT/fastp/S01/S01.R2.clean.fastq.gz" --host-index "$TMP_ROOT/db/host/GRCh38" \
  --outdir "$OUTPUT/host/S01" --threads 2 --task-id task-core --state-root "$OUTPUT"
assert_status "$OUTPUT/status/S01.host_depletion.json" succeeded

bash "$PROJECT_ROOT/workflow/bin/core/taxonomy.sh" \
  --sample S01 --r1 "$OUTPUT/host/S01/S01.R1.host_removed.fastq.gz" \
  --r2 "$OUTPUT/host/S01/S01.R2.host_removed.fastq.gz" \
  --database-manifest "$DATABASE_MANIFEST" \
  --outdir "$OUTPUT/taxonomy/S01" --threads 2 --task-id task-core --state-root "$OUTPUT"
assert_status "$OUTPUT/status/S01.taxonomy.json" succeeded

MOCK_HUMANN_ARGS="$TMP_ROOT/humann.args" \
bash "$PROJECT_ROOT/workflow/bin/core/functional_annotation.sh" \
  --sample S01 --r1 "$OUTPUT/host/S01/S01.R1.host_removed.fastq.gz" \
  --r2 "$OUTPUT/host/S01/S01.R2.host_removed.fastq.gz" \
  --database-manifest "$DATABASE_MANIFEST" \
  --outdir "$OUTPUT/function/S01" --threads 2 --task-id task-core --state-root "$OUTPUT"
assert_status "$OUTPUT/status/S01.functional_annotation.json" succeeded
grep -Fx -- '--metaphlan-options' "$TMP_ROOT/humann.args" >/dev/null
grep -Fx -- "--bowtie2db $TMP_ROOT/db/metaphlan --offline" "$TMP_ROOT/humann.args" >/dev/null
for output in \
  read_genefamilies.tsv read_pathabundance.tsv read_pathways.tsv \
  read_pathcoverage.tsv read_ko.tsv read_ec.tsv \
  humann_raw_genefamilies.tsv humann_raw_pathabundance.tsv \
  humann_raw_pathcoverage.tsv humann_raw_ko.tsv humann_raw_ec.tsv \
  functional.provenance.json; do
  test -s "$OUTPUT/function/S01/$output"
done
grep -Fx 'map_ko_uniref90.txt.gz' "$MOCK_REGROUP_LOG" >/dev/null
grep -Fx 'map_level4ec_uniref90.txt.gz' "$MOCK_REGROUP_LOG" >/dev/null
python3 - "$OUTPUT/function/S01/functional.provenance.json" \
  "$OUTPUT/function/S01/read_ko.tsv" "$OUTPUT/function/S01/read_ec.tsv" <<'PY'
import csv, json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
outputs = {path.rsplit("/", 1)[-1] for path in payload["output_files"]}
assert {"humann_raw_ko.tsv", "humann_raw_ec.tsv"} <= outputs
assert payload["tool_versions"]["humann_regroup_table"] == "humann v3.9 (bundled humann_regroup_table)"
assert payload["database_releases"]["ko_mapping"] == "ko-r1"
assert payload["database_releases"]["ec_mapping"] == "ec-r1"
ko = next(csv.DictReader(open(sys.argv[2], encoding="utf-8"), delimiter="\t"))
ec = next(csv.DictReader(open(sys.argv[3], encoding="utf-8"), delimiter="\t"))
assert ko["database_release"] == "ko-r1"
assert ec["database_release"] == "ec-r1"
PY

rm "$OUTPUT/function/S01/humann_raw_ko.tsv"
MOCK_HUMANN_ARGS="$TMP_ROOT/humann.args" \
bash "$PROJECT_ROOT/workflow/bin/core/functional_annotation.sh" \
  --sample S01 --r1 "$OUTPUT/host/S01/S01.R1.host_removed.fastq.gz" \
  --r2 "$OUTPUT/host/S01/S01.R2.host_removed.fastq.gz" \
  --database-manifest "$DATABASE_MANIFEST" \
  --outdir "$OUTPUT/function/S01" --threads 2 --task-id task-core \
  --state-root "$OUTPUT" --resume
test -s "$OUTPUT/function/S01/humann_raw_ko.tsv"
[[ "$(wc -l < "$MOCK_REGROUP_LOG")" -eq 4 ]]

MISSING_MAPPING_MANIFEST="$TMP_ROOT/database-manifest-missing-ko.json"
python3 - "$DATABASE_MANIFEST" "$MISSING_MAPPING_MANIFEST" <<'PY'
import json, sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
payload["databases"]["function_reads"].pop("ko_mapping")
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY
set +e
MOCK_HUMANN_ARGS="$TMP_ROOT/humann-missing-mapping.args" \
bash "$PROJECT_ROOT/workflow/bin/core/functional_annotation.sh" \
  --sample S01 --r1 "$OUTPUT/host/S01/S01.R1.host_removed.fastq.gz" \
  --r2 "$OUTPUT/host/S01/S01.R2.host_removed.fastq.gz" \
  --database-manifest "$MISSING_MAPPING_MANIFEST" \
  --outdir "$OUTPUT/function-missing-mapping/S01" --threads 2 \
  --task-id task-missing-mapping --state-root "$OUTPUT/function-missing-mapping" \
  >"$TMP_ROOT/missing-mapping.log" 2>&1
missing_mapping_rc=$?
set -e
[[ "$missing_mapping_rc" -eq 65 ]]
grep -F 'database manifest lacks function_reads.ko_mapping' "$TMP_ROOT/missing-mapping.log" >/dev/null
grep -F 'reason=missing_or_invalid_mapping_entry' "$TMP_ROOT/missing-mapping.log" >/dev/null

REPORT_WORK="$OUTPUT/report-stage"
mkdir -p "$REPORT_WORK"
(
  cd "$REPORT_WORK"
  bash "$PROJECT_ROOT/workflow/bin/core/report.sh" \
    --results-root "$OUTPUT" --outdir report --task-id task-core --state-root "$OUTPUT" \
    --nextflow-work-root "$TMP_ROOT" \
    --database-manifest "$DATABASE_MANIFEST" --input-manifest "$INPUT/samples.csv" \
    --parameters-base64 "e30=" --run-name test-run --project-root "$PROJECT_ROOT"
)
assert_status "$OUTPUT/status/global.report.json" succeeded
test -s "$REPORT_WORK/deliverables/task-core.tar.gz"

before="$(stat -c %Y "$OUTPUT/fastp/S01/S01.R1.clean.fastq.gz")"
sleep 1
bash "$PROJECT_ROOT/workflow/bin/core/fastp.sh" \
  --sample S01 --r1 "$INPUT/S01_R1.fastq" --r2 "$INPUT/S01_R2.fastq" \
  --outdir "$OUTPUT/fastp/S01" --threads 2 --task-id task-core --state-root "$OUTPUT" --resume
after="$(stat -c %Y "$OUTPUT/fastp/S01/S01.R1.clean.fastq.gz")"
[[ "$before" == "$after" ]]
grep -F checkpoint_hit "$OUTPUT/logs/S01.fastp.log" >/dev/null

MOCK_NEXTFLOW_ARGS="$TMP_ROOT/nextflow.args" \
  bash "$PROJECT_ROOT/workflow/run_pipeline.sh" \
  --manifest "$INPUT/samples.csv" --outdir "$OUTPUT/nxf" \
  --database-registry "$DATABASE_REGISTRY" --database-profile test \
  --resume --enable-mags

grep -Fx -- '-resume' "$TMP_ROOT/nextflow.args" >/dev/null
grep -Fx -- '--enable_mags' "$TMP_ROOT/nextflow.args" >/dev/null
grep -Fx -- 'true' "$TMP_ROOT/nextflow.args" >/dev/null

printf 'core command template tests passed\n'
