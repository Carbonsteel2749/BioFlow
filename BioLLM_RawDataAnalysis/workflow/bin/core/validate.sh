#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"

manifest=""
outdir=""
task_id="manual"
state_root=""

while (($#)); do
  case "$1" in
    --manifest) manifest="$2"; shift 2;;
    --outdir) outdir="$2"; shift 2;;
    --task-id) task_id="$2"; shift 2;;
    --state-root) state_root="$2"; shift 2;;
    --resume) PIPELINE_RESUME=1; shift;;
    *) echo "unknown argument: $1" >&2; exit 64;;
  esac
done

[[ -n "$manifest" && -n "$outdir" ]] || {
  echo "--manifest and --outdir are required" >&2
  exit 64
}

state_root="${state_root:-$(dirname "$outdir")}"
mkdir -p "$outdir"

export PIPELINE_TASK_ID="$task_id" PIPELINE_SAMPLE_ID="global"
export PIPELINE_LOG_DIR="$state_root/logs" PIPELINE_STATUS_DIR="$state_root/status"
step_prepare validate

output="$outdir/manifest.validated.csv"
diagnostic="$outdir/.manifest.validation.diagnostic.json"
validator="$SCRIPT_DIR/validate_manifest.py"

cleanup() {
  rm -f -- "$diagnostic"
}
trap cleanup EXIT

validation_detail() {
  python3 - "$diagnostic" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    print("stage=validation_runtime sample=global reason=validator did not produce a diagnostic record")
    raise SystemExit(0)

try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, ValueError) as error:
    print(f"stage=validation_runtime sample=global reason=unreadable diagnostic: {error}")
    raise SystemExit(0)

stage = payload.get("stage", "validation_runtime")
sample = payload.get("sample_id", "global")
reason = payload.get("reason", "unknown validation failure")
print(f"stage={stage} sample={sample} reason={reason}")
PY
}

validation_sample() {
  python3 - "$diagnostic" <<'PY'
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    print(payload.get("sample_id") or "global")
except (OSError, ValueError):
    print("global")
PY
}

validation_fail() {
  local exit_code="$1"
  local detail
  local failed_sample
  detail="$(validation_detail)"
  failed_sample="$(validation_sample)"
  trap - ERR
  PIPELINE_SAMPLE_ID="$failed_sample"
  export PIPELINE_SAMPLE_ID
  log_message ERROR "validation_failed exit_code=$exit_code $detail"
  status_write failed "$exit_code" "$detail" "${CURRENT_COMMAND:-python3 $validator}" "${BASH_LINENO[0]:-0}"
  exit "$exit_code"
}

validated_manifest_is_complete() {
  python3 - "$1" <<'PY'
import csv
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["sample_id", "read1", "read2"]:
            raise ValueError("manifest header is invalid")
        rows = list(reader)
        if not rows:
            raise ValueError("manifest has no samples")
        for row in rows:
            if set(row) != {"sample_id", "read1", "read2"}:
                raise ValueError("manifest columns are invalid")
            if not row["sample_id"] or not Path(row["read1"]).is_absolute() or not Path(row["read2"]).is_absolute():
                raise ValueError("manifest contains incomplete or relative paths")
except (OSError, ValueError, csv.Error) as error:
    print(error, file=sys.stderr)
    raise SystemExit(1)
PY
}

if checkpoint_valid "$output"; then
  if validated_manifest_is_complete "$output"; then
    exit 0
  fi
  log_message WARN "checkpoint_invalid reason=validated manifest is incomplete or malformed; rerunning"
fi

step_start "manifest validation started manifest=$manifest"
rm -f -- "$output"

if ! command -v python3 >/dev/null 2>&1; then
  trap - ERR
  log_message ERROR "validation_failed exit_code=127 stage=dependency sample=global reason=python3 is not available"
  status_write failed 127 "stage=dependency sample=global reason=python3 is not available" "python3" "${LINENO}"
  exit 127
fi

if run_tool python3 "$validator" --manifest "$manifest" --output "$output" --diagnostic "$diagnostic"; then
  :
else
  validation_fail "$?"
fi

if ! validated_manifest_is_complete "$output"; then
  trap - ERR
  log_message ERROR "validation_failed exit_code=66 stage=output_integrity sample=global reason=validated manifest is malformed after successful validation"
  status_write failed 66 "stage=output_integrity sample=global reason=validated manifest is malformed after successful validation" "${CURRENT_COMMAND:-python3 $validator}" "${LINENO}"
  exit 66
fi

python3 - "$diagnostic" <<'PY' | while IFS=$'\t' read -r sample r1_count r2_count; do
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for sample in payload.get("samples", []):
    print(f"{sample['sample_id']}\t{sample['r1_reads']}\t{sample['r2_reads']}")
PY
  log_message INFO "sample=$sample r1_reads=$r1_count r2_reads=$r2_count"
done

step_success "manifest validation completed" "$output"
