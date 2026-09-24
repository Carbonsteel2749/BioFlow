#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_ROOT="$(mktemp -d)"; trap 'rm -rf "$TMP_ROOT"' EXIT
MOCK_BIN="$TMP_ROOT/mock-bin"; mkdir -p "$MOCK_BIN"
cat > "$MOCK_BIN/metawrap" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
module="$1"; shift
outdir=""; completeness=70; contamination=5
while (($#)); do
  case "$1" in
    -o) outdir="$2"; shift 2;;
    -c) completeness="$2"; shift 2;;
    -x) contamination="$2"; shift 2;;
    *) shift;;
  esac
done
mkdir -p "$outdir"
case "$module" in
  assembly)
    printf '>contig_1\nACGTACGTACGT\n' > "$outdir/final_assembly.fasta"
    printf '<!doctype html><html><body>assembly passed</body></html>\n' > "$outdir/assembly_report.html";;
  binning)
    for name in metabat2_bins maxbin2_bins concoct_bins; do
      mkdir -p "$outdir/$name"
      printf '>bin_1\nACGTACGT\n' > "$outdir/$name/bin.1.fa"
    done;;
  bin_refinement)
    mkdir -p "$outdir/metawrap_${completeness}_${contamination}_bins"
    printf '>bin_1\nACGTACGT\n' > "$outdir/metawrap_${completeness}_${contamination}_bins/bin.1.fa"
    printf 'bin_id\tcompleteness\tcontamination\nbin.1\t95\t1\n' > "$outdir/metawrap_${completeness}_${contamination}_bins.stats";;
  quant_bins)
    printf 'bin_id\tS01\nbin.1\t0.75\n' > "$outdir/bin_abundance_table.tab";;
  reassemble_bins)
    mkdir -p "$outdir/reassembled_bins"
    printf '>bin_1\nACGTACGT\n' > "$outdir/reassembled_bins/bin.1.fa"
    printf 'bin_id\tstatus\nbin.1\tPASS\n' > "$outdir/reassembled_bins.stats";;
  classify_bins)
    printf 'bin_id\ttaxonomy\nbin.1\td__Bacteria;p__Proteobacteria\n' > "$outdir/bin_taxonomy.tab";;
  annotate_bins)
    mkdir -p "$outdir/bin_funct_annotations"
    printf '##gff-version 3\nbin.1\tmock\tgene\t1\t8\t.\t+\t.\tID=gene1\n' > "$outdir/bin_funct_annotations/bin.1.gff";;
  *) echo "unknown metawrap module: $module" >&2; exit 64;;
esac
MOCK
chmod +x "$MOCK_BIN/metawrap"; export PATH="$MOCK_BIN:$PATH"

assert_status() {
  python3 - "$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle: payload=json.load(handle)
if payload["status"] != "succeeded": raise SystemExit(payload)
PY
}

INPUT="$TMP_ROOT/input"; OUTPUT="$TMP_ROOT/output"; mkdir -p "$INPUT" "$OUTPUT"
printf '@r1\nACGT\n+\n!!!!\n' | gzip -c > "$INPUT/S01.R1.fastq.gz"
printf '@r2\nTGCA\n+\n!!!!\n' | gzip -c > "$INPUT/S01.R2.fastq.gz"
printf '%s\n' "$INPUT/S01.R1.fastq.gz" > "$INPUT/r1.list"
printf '%s\n' "$INPUT/S01.R2.fastq.gz" > "$INPUT/r2.list"
printf '%s\n%s\n' "$INPUT/S01.R1.fastq.gz" "$INPUT/S01.R2.fastq.gz" > "$INPUT/reads.list"
printf 'sample_id,read1,read2\nS01,%s,%s\n' \
 "$INPUT/S01.R1.fastq.gz" "$INPUT/S01.R2.fastq.gz" > "$INPUT/samples.csv"
python3 - "$INPUT/database.resolved.json" "$TMP_ROOT" <<'PY'
import json
import sys

output, root = sys.argv[1:]
entry = {
    "database_name": "MAG test database",
    "purpose": "MAG annotation test",
    "path": root,
    "release": "test-r1",
    "taxonomy_system": "GTDB",
    "manifest_sha256": "test-manifest-sha256",
    "tool_compatibility_version": "test-v1",
    "required_sentinel_files": [],
}
payload = {
    "schema_version": 1,
    "database_profile": "test",
    "resolved_manifest_sha256": "test-resolved-sha256",
    "databases": {
        "mag_annotation": {
            "classification": entry,
            "function": dict(entry),
        }
    },
}
json.dump(payload, open(output, "w"), indent=2)
PY

bash "$PROJECT_ROOT/workflow/bin/mag/assembly.sh" --r1-list "$INPUT/r1.list" --r2-list "$INPUT/r2.list" \
 --outdir "$OUTPUT/assembly" --threads 2 --memory-gb 4 --task-id task-mag --state-root "$OUTPUT"
assert_status "$OUTPUT/status/global.assembly.json"

bash "$PROJECT_ROOT/workflow/bin/mag/binning.sh" --assembly "$OUTPUT/assembly/final_assembly.fasta" \
 --reads-list "$INPUT/reads.list" --outdir "$OUTPUT/binning" --threads 2 --task-id task-mag --state-root "$OUTPUT"
assert_status "$OUTPUT/status/global.binning.json"

bash "$PROJECT_ROOT/workflow/bin/mag/bin_refinement.sh" \
 --bins-a "$OUTPUT/binning/metabat2_bins" --bins-b "$OUTPUT/binning/maxbin2_bins" --bins-c "$OUTPUT/binning/concoct_bins" \
 --outdir "$OUTPUT/refinement" --threads 2 --completeness 70 --contamination 5 --task-id task-mag --state-root "$OUTPUT"
assert_status "$OUTPUT/status/global.bin_refinement.json"

BINS="$OUTPUT/refinement/metawrap_70_5_bins"
bash "$PROJECT_ROOT/workflow/bin/mag/bin_quantification.sh" --bins "$BINS" \
 --assembly "$OUTPUT/assembly/final_assembly.fasta" --reads-list "$INPUT/reads.list" \
 --sample-manifest "$INPUT/samples.csv" --database-manifest "$INPUT/database.resolved.json" \
 --outdir "$OUTPUT/quant" --threads 2 --task-id task-mag --state-root "$OUTPUT"
assert_status "$OUTPUT/status/global.bin_quantification.json"

bash "$PROJECT_ROOT/workflow/bin/mag/bin_reassembly.sh" --bins "$BINS" \
 --r1-list "$INPUT/r1.list" --r2-list "$INPUT/r2.list" --outdir "$OUTPUT/reassembly" \
 --threads 2 --memory-gb 4 --completeness 70 --contamination 5 --task-id task-mag --state-root "$OUTPUT"
assert_status "$OUTPUT/status/global.bin_reassembly.json"

bash "$PROJECT_ROOT/workflow/bin/mag/bin_annotation.sh" --bins "$OUTPUT/reassembly/reassembled_bins" \
 --cohort-id cohort-A \
 --database-manifest "$INPUT/database.resolved.json" \
 --outdir "$OUTPUT/annotation" --threads 2 --task-id task-mag --state-root "$OUTPUT"
assert_status "$OUTPUT/status/cohort-A.bin_annotation.json"

test -s "$OUTPUT/quant/bin_abundance_table.tab"
test -s "$OUTPUT/quant/sample_mag_abundance.tsv"
test -s "$OUTPUT/annotation/classification/bin_taxonomy.tab"
test -s "$OUTPUT/annotation/function/bin_funct_annotations/bin.1.gff"
test -s "$OUTPUT/annotation/mag_annotation.tsv"
test -s "$OUTPUT/annotation/mag_function_annotation.tsv"
test -s "$OUTPUT/annotation/mag_annotation.provenance.json"
python3 - "$OUTPUT/quant/sample_mag_abundance.tsv" \
  "$OUTPUT/annotation/mag_annotation.tsv" \
  "$OUTPUT/annotation/mag_function_annotation.tsv" \
  "$OUTPUT/annotation/mag_annotation.provenance.json" <<'PY'
import csv
import json
import sys

abundance_path, annotation_path, function_path, provenance_path = sys.argv[1:]
abundance = list(csv.DictReader(open(abundance_path, encoding="utf-8"), delimiter="\t"))
annotation = list(csv.DictReader(open(annotation_path, encoding="utf-8"), delimiter="\t"))
assert abundance == [{
    "sample_id": "S01",
    "mag_id": "bin.1",
    "detected": "true",
    "abundance": "0.75",
    "abundance_unit": "tool_reported_abundance",
    "coverage": "",
    "mapped_reads": "",
    "mapping_method": "MetaWRAP quant_bins",
    "detection_threshold": "> 0 tool-reported abundance",
    "evidence_source": "MetaWRAP bin quantification",
    "association_meaning": "MAG detected in sample by read mapping",
    "provenance_id": "task-mag:mag_quantification",
}]
assert len(annotation) == 1
assert annotation[0]["cohort_id"] == "cohort-A"
assert annotation[0]["mag_id"] == "bin.1"
assert "sample_id" not in annotation[0]
assert annotation[0]["taxonomy_system"] == "GTDB"
assert annotation[0]["genome_size"] == "8"
assert annotation[0]["contig_count"] == "1"
functions = list(csv.DictReader(open(function_path, encoding="utf-8"), delimiter="\t"))
assert functions == []
provenance = json.load(open(provenance_path, encoding="utf-8"))
for key in ("cohort_id", "databases", "database_manifest_sha256", "tool_name"):
    assert provenance[key]
assert provenance["cohort_id"] == "cohort-A"
assert provenance["databases"]["classification"]["database_release"] == "test-r1"
assert provenance["database_manifest_sha256"] == "test-resolved-sha256"
PY

MISSING_DATABASE_MANIFEST="$INPUT/database-missing-mag-path.json"
python3 - "$INPUT/database.resolved.json" "$MISSING_DATABASE_MANIFEST" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
payload["databases"]["mag_annotation"]["function"]["path"] = ""
json.dump(payload, open(sys.argv[2], "w"), indent=2)
PY
set +e
bash "$PROJECT_ROOT/workflow/bin/mag/bin_annotation.sh" --cohort-id cohort-A --bins "$OUTPUT/reassembly/reassembled_bins" \
 --database-manifest "$MISSING_DATABASE_MANIFEST" --outdir "$OUTPUT/annotation-missing-db" --threads 2 --task-id task-mag --state-root "$OUTPUT" \
 >"$TMP_ROOT/missing-mag-db.log" 2>&1
missing_database_rc=$?
set -e
[[ "$missing_database_rc" -eq 65 ]]
grep -F 'reason=database_profile_or_path_missing' "$TMP_ROOT/missing-mag-db.log" >/dev/null
printf 'MAG template tests passed\n'
