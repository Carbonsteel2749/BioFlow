#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export BIOLLM_INPUT_ROOT="${BIOLLM_INPUT_ROOT:-$PROJECT_ROOT/runtime/incoming}"
export BIOLLM_STATE_ROOT="${BIOLLM_STATE_ROOT:-$PROJECT_ROOT/runtime}"
export BIOLLM_WORKFLOW_PATH="${BIOLLM_WORKFLOW_PATH:-$PROJECT_ROOT/workflow/main.nf}"
if [[ -x /home/xh/.local/bin/nextflow ]]; then
  default_nextflow=/home/xh/.local/bin/nextflow
else
  default_nextflow=nextflow
fi
export BIOLLM_NEXTFLOW_BIN="${BIOLLM_NEXTFLOW_BIN:-$default_nextflow}"
export BIOLLM_HOST_INDEX="${BIOLLM_HOST_INDEX:-/home/xh/databases/host/GRCh38_noalt/index/GRCh38_noalt_as}"
export BIOLLM_KRAKEN_DB="${BIOLLM_KRAKEN_DB:-/home/xh/databases/kraken2/standard_8gb_20260626/index}"
export BIOLLM_HUMANN_NUCLEOTIDE_DB="${BIOLLM_HUMANN_NUCLEOTIDE_DB:-/home/xh/databases/humann/chocophlan}"
export BIOLLM_HUMANN_PROTEIN_DB="${BIOLLM_HUMANN_PROTEIN_DB:-/home/xh/databases/humann/uniref}"
export BIOLLM_METAPHLAN_DB="${BIOLLM_METAPHLAN_DB:-/home/xh/databases/metaphlan/mpa_vJun23_CHOCOPhlAnSGB_202403/index}"
export BIOLLM_DATABASE_REGISTRY="${BIOLLM_DATABASE_REGISTRY:-$PROJECT_ROOT/config/database-registry.local.json}"
export BIOLLM_DATABASE_PROFILE="${BIOLLM_DATABASE_PROFILE:-server-v1}"
if [[ -z "${BIOLLM_DATABASE_MANIFEST:-}" ]]; then
  resolved_database_manifest="$BIOLLM_STATE_ROOT/config/database.resolved.json"
  mkdir -p "$(dirname "$resolved_database_manifest")"
  python3 "$PROJECT_ROOT/workflow/bin/core/validate_databases.py" \
    --registry "$BIOLLM_DATABASE_REGISTRY" \
    --profile "$BIOLLM_DATABASE_PROFILE" \
    --output "$resolved_database_manifest"
  export BIOLLM_DATABASE_MANIFEST="$resolved_database_manifest"
elif [[ ! -f "$BIOLLM_DATABASE_MANIFEST" ]]; then
  printf 'BIOLLM_DATABASE_MANIFEST is not a readable file: %s\n' \
    "$BIOLLM_DATABASE_MANIFEST" >&2
  exit 66
fi
exec python3 -m uvicorn backend.app.main:create_app \
  --factory \
  --host "${BIOLLM_HOST:-127.0.0.1}" \
  --port "${BIOLLM_PORT:-8000}"
