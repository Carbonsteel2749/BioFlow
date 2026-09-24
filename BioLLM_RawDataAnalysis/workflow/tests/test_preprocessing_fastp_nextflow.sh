#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if ! command -v nextflow >/dev/null 2>&1; then
  printf 'SKIP: nextflow is not installed; fastp module smoke test not run\n'
  exit 0
fi

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT
MOCK_BIN="$TMP_ROOT/mock-bin"
mkdir -p "$MOCK_BIN" "$TMP_ROOT/input" "$TMP_ROOT/output"

cat > "$MOCK_BIN/fastp" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
out1=""; out2=""; json=""; html=""
while (($#)); do
  case "$1" in
    --out1) out1="$2"; shift 2;;
    --out2) out2="$2"; shift 2;;
    --json) json="$2"; shift 2;;
    --html) html="$2"; shift 2;;
    *) shift;;
  esac
done
printf '@read001/1\nACGT\n+\n!!!!\n' | gzip -c > "$out1"
printf '@read001/2\nTGCA\n+\n####\n' | gzip -c > "$out2"
printf '{"summary":{"before_filtering":{"total_reads":2},"after_filtering":{"total_reads":1}}}' > "$json"
printf '<html>fastp</html>\n' > "$html"
MOCK
chmod +x "$MOCK_BIN/fastp"

printf '@read001/1\nACGT\n+\n!!!!\n' > "$TMP_ROOT/input/S01_R1.fastq"
printf '@read001/2\nTGCA\n+\n####\n' > "$TMP_ROOT/input/S01_R2.fastq"
printf 'sample_id,read1,read2\nS01,%s,%s\n' "$TMP_ROOT/input/S01_R1.fastq" "$TMP_ROOT/input/S01_R2.fastq" > "$TMP_ROOT/input/samples.csv"

PATH="$MOCK_BIN:$PATH" nextflow run "$PROJECT_ROOT/workflow/fastp_smoke.nf" \
  --input_manifest "$TMP_ROOT/input/samples.csv" --outdir "$TMP_ROOT/output" \
  --task_id fastp-nextflow-test --threads 2 -work-dir "$TMP_ROOT/work"

test -s "$TMP_ROOT/output/fastp/S01/S01.R1.clean.fastq.gz"
test -s "$TMP_ROOT/output/fastp/S01/S01.R2.clean.fastq.gz"
python3 - "$TMP_ROOT/output/fastp/S01/S01.fastp.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
assert payload["summary"]["after_filtering"]["total_reads"] == 1
PY
python3 - "$TMP_ROOT/output/status/S01.fastp.json" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
if payload["status"] != "succeeded":
    raise SystemExit(payload)
PY
printf 'preprocessing fastp Nextflow smoke test passed\n'
