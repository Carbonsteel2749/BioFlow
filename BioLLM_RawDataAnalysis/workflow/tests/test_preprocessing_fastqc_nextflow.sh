#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if ! command -v nextflow >/dev/null 2>&1; then
  printf 'SKIP: nextflow is not installed; FastQC module smoke test not run\n'
  exit 0
fi

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
MOCK_BIN="$TMP_ROOT/mock-bin"
mkdir -p "$MOCK_BIN" "$TMP_ROOT/input" "$TMP_ROOT/output"

cat > "$MOCK_BIN/fastqc" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
outdir=""
inputs=()
while (($#)); do
  case "$1" in
    --outdir) outdir="$2"; shift 2;;
    --threads) shift 2;;
    *) inputs+=("$1"); shift;;
  esac
done
mkdir -p "$outdir"
for input in "${inputs[@]}"; do
  base="$(basename "$input")"
  base="${base%.gz}"; base="${base%.fastq}"; base="${base%.fq}"
  printf '<html>FastQC</html>\n' > "$outdir/${base}_fastqc.html"
  python3 - "$outdir/${base}_fastqc.zip" "$base" <<'PY'
import sys
import zipfile
with zipfile.ZipFile(sys.argv[1], "w") as archive:
    archive.writestr(f"{sys.argv[2]}_fastqc/summary.txt", "PASS\tBasic Statistics\n")
    archive.writestr(f"{sys.argv[2]}_fastqc/fastqc_data.txt", ">>Basic Statistics\tpass\n")
PY
done
MOCK
chmod +x "$MOCK_BIN/fastqc"

printf '@read001/1\nACGT\n+\n!!!!\n' > "$TMP_ROOT/input/S01_R1.fastq"
printf '@read001/2\nTGCA\n+\n####\n' > "$TMP_ROOT/input/S01_R2.fastq"
printf 'sample_id,read1,read2\nS01,%s,%s\n' "$TMP_ROOT/input/S01_R1.fastq" "$TMP_ROOT/input/S01_R2.fastq" > "$TMP_ROOT/input/samples.csv"

PATH="$MOCK_BIN:$PATH" nextflow run "$PROJECT_ROOT/workflow/fastqc_smoke.nf" \
  --input_manifest "$TMP_ROOT/input/samples.csv" \
  --outdir "$TMP_ROOT/output" \
  --task_id fastqc-nextflow-test \
  --threads 2 \
  -work-dir "$TMP_ROOT/work"

test -s "$TMP_ROOT/output/fastqc/S01/S01_R1_fastqc.html"
test -s "$TMP_ROOT/output/fastqc/S01/S01_R2_fastqc.html"
python3 - "$TMP_ROOT/output/fastqc/S01/S01_R1_fastqc.zip" <<'PY'
import sys
import zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    assert archive.testzip() is None
PY
python3 - "$TMP_ROOT/output/status/S01.fastqc_raw.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
if payload["status"] != "succeeded":
    raise SystemExit(payload)
PY
printf 'preprocessing FastQC Nextflow smoke test passed\n'
