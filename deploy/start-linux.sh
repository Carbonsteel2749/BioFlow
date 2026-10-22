#!/usr/bin/env bash
set -euo pipefail
BIOFLOW_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BIOFLOW_ROOT"
if [[ -f deploy/bioflow.env ]]; then
  set -a
  source deploy/bioflow.env
  set +a
fi
exec .venv/bin/python scripts/start.py --with-services
