#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NEXTFLOW_BIN="${NEXTFLOW_BIN:-/home/xh/.local/bin/nextflow}"
if [[ ! -x "$NEXTFLOW_BIN" ]]; then
  printf 'SKIP: Nextflow executable not found at %s\n' "$NEXTFLOW_BIN"
  exit 0
fi

TMP_ROOT="$(mktemp -d)"
cleanup() {
  if [[ "${KEEP_INTEGRATION_TMP:-0}" == 1 ]]; then
    printf 'integration test workspace retained at %s\n' "$TMP_ROOT"
  else
    rm -rf "$TMP_ROOT"
  fi
}
trap cleanup EXIT
FIXTURE_SOURCE="$PROJECT_ROOT/workflow/tests/fixtures/integration"
FIXTURE_ROOT="$TMP_ROOT/fixture"
MOCK_BIN="$TMP_ROOT/mock-bin"
MOCK_CALL_LOG="$TMP_ROOT/mock-calls.tsv"
ASSERTIONS="$PROJECT_ROOT/workflow/tests/helpers/assert_integration_outputs.py"
export MOCK_BIN MOCK_CALL_LOG
cp -R "$FIXTURE_SOURCE" "$FIXTURE_ROOT"
: > "$MOCK_CALL_LOG"
source "$PROJECT_ROOT/workflow/tests/helpers/mock_bio_tools.sh"
export PATH="$MOCK_BIN:$PATH"
export NXF_ANSI_LOG=false

DATABASE_ROOT="$TMP_ROOT/databases"
DATABASE_REGISTRY="$TMP_ROOT/database-registry.json"
HOST_INDEX="$DATABASE_ROOT/host/GRCh38"
mkdir -p \
  "$DATABASE_ROOT/kraken" \
  "$DATABASE_ROOT/host" \
  "$DATABASE_ROOT/humann/chocophlan" \
  "$DATABASE_ROOT/humann/uniref" \
  "$DATABASE_ROOT/humann/utility" \
  "$DATABASE_ROOT/metaphlan" \
  "$DATABASE_ROOT/mag/classification" \
  "$DATABASE_ROOT/mag/function"
for shard in 1 2 3 4 rev.1 rev.2; do
  : > "$HOST_INDEX.${shard}.bt2"
done
touch \
  "$DATABASE_ROOT/kraken/hash.k2d" \
  "$DATABASE_ROOT/kraken/opts.k2d" \
  "$DATABASE_ROOT/kraken/taxo.k2d" \
  "$DATABASE_ROOT/humann/utility/map_ko_uniref90.txt.gz" \
  "$DATABASE_ROOT/humann/utility/map_level4ec_uniref90.txt.gz"

python3 - "$DATABASE_REGISTRY" "$DATABASE_ROOT" <<'PY'
import json
import sys

output, root = sys.argv[1:]

def entry(name, purpose, path, taxonomy="not_applicable", sentinels=()):
    return {
        "database_name": name,
        "purpose": purpose,
        "path": f"{root}/{path}",
        "release": "integration-r1",
        "taxonomy_system": taxonomy,
        "manifest_sha256": "integration-source-sha256",
        "tool_compatibility_version": "integration-v1",
        "required_sentinel_files": list(sentinels),
    }

kraken = entry(
    "Kraken2 integration database",
    "read taxonomic classification",
    "kraken",
    "NCBI",
    ("hash.k2d", "opts.k2d", "taxo.k2d"),
)
profile = {
    "taxonomy_reads": {"kraken2": kraken, "bracken": dict(kraken)},
    "function_reads": {
        "humann_nucleotide": entry("ChocoPhlAn integration", "nucleotide search", "humann/chocophlan"),
        "humann_protein": entry("UniRef integration", "translated search", "humann/uniref"),
        "humann_utility": entry("HUMAnN utility integration", "name mapping", "humann/utility"),
        "ko_mapping": entry(
            "UniRef90 to KO integration",
            "KO conversion",
            "humann/utility",
            sentinels=("map_ko_uniref90.txt.gz",),
        ),
        "ec_mapping": entry(
            "UniRef90 to EC integration",
            "EC conversion",
            "humann/utility",
            sentinels=("map_level4ec_uniref90.txt.gz",),
        ),
        "metaphlan": entry("MetaPhlAn integration", "prescreen", "metaphlan", "MetaPhlAn SGB"),
    },
    "mag_annotation": {
        "classification": entry("MAG taxonomy integration", "MAG classification", "mag/classification", "GTDB"),
        "function": entry("MAG function integration", "MAG functional annotation", "mag/function"),
    },
}
json.dump({"schema_version": 1, "profiles": {"integration": profile}}, open(output, "w"), indent=2)
PY

MUTABLE_MANIFEST="$FIXTURE_ROOT/manifests/samples.mutable.csv"
head -n 2 "$FIXTURE_ROOT/manifests/samples.relative.csv" > "$MUTABLE_MANIFEST"
CORE_OUTPUT="$TMP_ROOT/core-output"

run_core() {
  local resume_flag="${1:-}"
  args=(
    --manifest "$MUTABLE_MANIFEST"
    --outdir "$CORE_OUTPUT"
    --task-id integration-core
    --database-registry "$DATABASE_REGISTRY"
    --database-profile integration
    --host-index "$HOST_INDEX"
    --nextflow-bin "$NEXTFLOW_BIN"
  )
  [[ "$resume_flag" == resume ]] && args+=(--resume)
  bash "$PROJECT_ROOT/workflow/run_pipeline.sh" "${args[@]}"
}

run_isolated() {
  local manifest="$1"
  local output="$2"
  local task_id="$3"
  local resume_flag="${4:-}"
  local extra_args=()
  if [[ "$resume_flag" == resume ]]; then
    extra_args=("${@:5}")
  else
    resume_flag=""
    extra_args=("${@:4}")
  fi
  args=(
    --manifest "$manifest"
    --outdir "$output"
    --task-id "$task_id"
    --database-registry "$DATABASE_REGISTRY"
    --database-profile integration
    --host-index "$HOST_INDEX"
    --nextflow-bin "$NEXTFLOW_BIN"
  )
  [[ "$resume_flag" == resume ]] && args+=(--resume)
  args+=("${extra_args[@]}")
  bash "$PROJECT_ROOT/workflow/run_pipeline.sh" "${args[@]}"
}

compute_call_count() {
  awk -F '\t' '
    $1 !~ /^(fastqc|fastp|bowtie2|samtools|kraken2|bracken|humann|humann_regroup_table)$/ { next }
    $2 == "--version" { next }
    $2 == "-v" { next }
    { count += 1 }
    END { print count + 0 }
  ' "$MOCK_CALL_LOG"
}

tool_call_count() {
  local tool="$1"
  awk -F '\t' -v tool="$tool" '
    $1 == tool && $2 != "--version" { count += 1 }
    END { print count + 0 }
  ' "$MOCK_CALL_LOG"
}

run_core
python3 "$ASSERTIONS" manifest "$CORE_OUTPUT/validate/validated/manifest.validated.csv" S01
python3 "$ASSERTIONS" package "$CORE_OUTPUT/deliverables/integration-core.tar.gz" S01
[[ ! -e "$CORE_OUTPUT/mag" ]]

calls_before_resume="$(compute_call_count)"
database_sha_before_resume="$(sha256sum "$CORE_OUTPUT/.pipeline/database.resolved.json" | awk '{print $1}')"
database_mtime_before_resume="$(stat -c %Y "$CORE_OUTPUT/.pipeline/database.resolved.json")"
run_core resume
taxonomy_calls_after_unchanged_resume="$(tool_call_count kraken2)"
fastp_calls_after_unchanged_resume="$(tool_call_count fastp)"
calls_after_resume="$(compute_call_count)"
database_sha_after_resume="$(sha256sum "$CORE_OUTPUT/.pipeline/database.resolved.json" | awk '{print $1}')"
database_mtime_after_resume="$(stat -c %Y "$CORE_OUTPUT/.pipeline/database.resolved.json")"
[[ "$database_sha_before_resume" == "$database_sha_after_resume" ]]
[[ "$database_mtime_before_resume" == "$database_mtime_after_resume" ]]
[[ "$calls_before_resume" == "$calls_after_resume" ]] || {
  printf 'unchanged -resume run executed tools again: before=%s after=%s\n' \
    "$calls_before_resume" "$calls_after_resume" >&2
  exit 1
}

tail -n 1 "$FIXTURE_ROOT/manifests/samples.relative.csv" >> "$MUTABLE_MANIFEST"
run_core resume
calls_after_manifest_change="$(compute_call_count)"
(( calls_after_manifest_change > calls_after_resume ))
python3 "$ASSERTIONS" manifest "$CORE_OUTPUT/validate/validated/manifest.validated.csv" S01,S02
python3 "$ASSERTIONS" package "$CORE_OUTPUT/deliverables/integration-core.tar.gz" S01,S02
grep -F $'fastp\t' "$MOCK_CALL_LOG" | grep -F 'S02' >/dev/null

fastp_before_database_change="$(tool_call_count fastp)"
taxonomy_before_database_change="$(tool_call_count kraken2)"
python3 - "$DATABASE_REGISTRY" <<'PY'
import json
import sys

path = sys.argv[1]
registry = json.load(open(path, encoding="utf-8"))
for group in registry["profiles"]["integration"].values():
    for entry in group.values():
        entry["release"] = "integration-r2"
        entry["manifest_sha256"] = "integration-source-sha256-r2"
json.dump(registry, open(path, "w", encoding="utf-8"), indent=2)
PY
run_core resume
[[ "$(tool_call_count fastp)" == "$fastp_before_database_change" ]] || {
  printf 'database manifest change incorrectly reran fastp\n' >&2; exit 1;
}
(( $(tool_call_count kraken2) > taxonomy_before_database_change )) || {
  printf 'database manifest change did not invalidate taxonomy cache\n' >&2; exit 1;
}

PARALLEL_ONE="$TMP_ROOT/parallel-one"
PARALLEL_TWO="$TMP_ROOT/parallel-two"
run_isolated "$MUTABLE_MANIFEST" "$PARALLEL_ONE" integration-parallel-one & parallel_one_pid=$!
run_isolated "$MUTABLE_MANIFEST" "$PARALLEL_TWO" integration-parallel-two & parallel_two_pid=$!
wait "$parallel_one_pid"
wait "$parallel_two_pid"
python3 "$ASSERTIONS" package "$PARALLEL_ONE/deliverables/integration-parallel-one.tar.gz" S01,S02
python3 "$ASSERTIONS" package "$PARALLEL_TWO/deliverables/integration-parallel-two.tar.gz" S01,S02

MEDIUM_MANIFEST="$TMP_ROOT/medium.csv"
MEDIUM_READS=50000
python3 - "$FIXTURE_ROOT/reads" "$MEDIUM_MANIFEST" "$MEDIUM_READS" <<'PY'
import sys
from pathlib import Path

reads, manifest, count = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
r1, r2 = reads / "S03_R1.fastq", reads / "S03_R2.fastq"
with r1.open("w", encoding="utf-8") as one, r2.open("w", encoding="utf-8") as two:
    for index in range(count):
        name = f"medium_{index:06d}"
        one.write(f"@{name}/1\nACGTACGT\n+\nIIIIIIII\n")
        two.write(f"@{name}/2\nTGCATGCA\n+\nIIIIIIII\n")
manifest.write_text(f"sample_id,read1,read2\nS03,{r1},{r2}\n", encoding="utf-8")
PY
MEDIUM_OUTPUT="$TMP_ROOT/medium-output"
run_isolated "$MEDIUM_MANIFEST" "$MEDIUM_OUTPUT" integration-medium
python3 "$ASSERTIONS" manifest "$MEDIUM_OUTPUT/validate/validated/manifest.validated.csv" S03
python3 "$ASSERTIONS" package "$MEDIUM_OUTPUT/deliverables/integration-medium.tar.gz" S03
grep -F "sample=S03 r1_reads=$MEDIUM_READS r2_reads=$MEDIUM_READS" "$MEDIUM_OUTPUT/logs/global.validate.log" >/dev/null

DISK_OUTPUT="$TMP_ROOT/disk-output"
set +e
run_isolated "$MUTABLE_MANIFEST" "$DISK_OUTPUT" integration-disk --min-free-gb 9999999
disk_exit=$?
set -e
[[ "$disk_exit" == 75 ]] || { printf 'disk preflight exit was %s, expected 75\n' "$disk_exit" >&2; exit 1; }
python3 - "$DISK_OUTPUT/status/global.pipeline.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["status"] == "failed", payload
assert payload["exit_code"] == 75, payload
PY
grep -F 'insufficient_disk_space' "$DISK_OUTPUT/logs/global.pipeline.log" >/dev/null
[[ ! -e "$DISK_OUTPUT/deliverables/integration-disk.tar.gz" ]]

DATABASE_OUTPUT="$TMP_ROOT/database-output"
set +e
bash "$PROJECT_ROOT/workflow/run_pipeline.sh" \
  --manifest "$MUTABLE_MANIFEST" \
  --outdir "$DATABASE_OUTPUT" \
  --task-id integration-database-missing \
  --database-registry "$DATABASE_REGISTRY" \
  --database-profile missing \
  --host-index "$HOST_INDEX" \
  --nextflow-bin "$NEXTFLOW_BIN"
database_exit=$?
set -e
[[ "$database_exit" == 65 ]] || { printf 'database preflight exit was %s, expected 65\n' "$database_exit" >&2; exit 1; }
python3 - "$DATABASE_OUTPUT/status/global.pipeline.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
assert payload["status"] == "failed", payload
assert payload["exit_code"] == 65, payload
PY
grep -F 'database preflight failed: database profile does not exist: missing' "$DATABASE_OUTPUT/logs/global.pipeline.log" >/dev/null
[[ ! -e "$DATABASE_OUTPUT/provenance/database.resolved.json" ]]

TOOL_OUTPUT="$TMP_ROOT/tool-output"
set +e
MOCK_FAIL_TOOL=fastp MOCK_FAIL_EXIT_CODE=42 run_isolated "$MUTABLE_MANIFEST" "$TOOL_OUTPUT" integration-tool-failure
tool_exit=$?
set -e
[[ "$tool_exit" == 1 ]] || { printf 'workflow exit was %s, expected 1\n' "$tool_exit" >&2; exit 1; }
python3 - "$TOOL_OUTPUT/status" "$TOOL_OUTPUT/logs" <<'PY'
import json
import sys
from pathlib import Path

status_dir = Path(sys.argv[1])
log_dir = Path(sys.argv[2])
payloads = [
    json.loads(path.read_text(encoding="utf-8"))
    for path in sorted(status_dir.glob("*.fastp.json"))
]
failed = [
    payload
    for payload in payloads
    if payload["status"] == "failed" and payload["exit_code"] == 42
]
assert failed, payloads
assert all("stage=tool_execution" in payload["message"] for payload in failed), failed
assert any(
    "fastp_failed exit_code=42 stage=tool_execution" in path.read_text(encoding="utf-8")
    for path in log_dir.glob("*.fastp.log")
), list(log_dir.glob("*.fastp.log"))
PY
[[ ! -e "$TOOL_OUTPUT/deliverables/integration-tool-failure.tar.gz" ]]

MAG_OUTPUT="$TMP_ROOT/mag-output"
bash "$PROJECT_ROOT/workflow/run_pipeline.sh" \
  --manifest "$MUTABLE_MANIFEST" \
  --outdir "$MAG_OUTPUT" \
  --task-id integration-mag \
  --database-registry "$DATABASE_REGISTRY" \
  --database-profile integration \
  --host-index "$HOST_INDEX" \
  --enable-mags \
  --nextflow-bin "$NEXTFLOW_BIN"

python3 "$ASSERTIONS" manifest "$MAG_OUTPUT/validate/validated/manifest.validated.csv" S01,S02
python3 "$ASSERTIONS" package \
  "$MAG_OUTPUT/deliverables/integration-mag.tar.gz" S01,S02 --require-mag
grep -F $'metawrap\tassembly ' "$MOCK_CALL_LOG" >/dev/null
grep -F $'metawrap\tbinning ' "$MOCK_CALL_LOG" >/dev/null
grep -F $'metawrap\tquant_bins ' "$MOCK_CALL_LOG" >/dev/null
grep -F $'metawrap\tclassify_bins ' "$MOCK_CALL_LOG" >/dev/null

printf 'full workflow integration tests passed\n'
