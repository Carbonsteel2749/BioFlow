#!/usr/bin/env bash
set -euo pipefail
BIOFLOW_PACKAGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIOFLOW_SERVER_PYTHON="${BIOFLOW_PYTHON:-/home/xh/BioLLM_RawDataAnalysis/.venv/bin/python}"
if [[ ! -x "$BIOFLOW_SERVER_PYTHON" ]]; then
  printf 'Python environment not found: %s\n' "$BIOFLOW_SERVER_PYTHON" >&2
  exit 1
fi
cd "$BIOFLOW_PACKAGE_ROOT"
if [[ $# -eq 0 ]]; then set -- start; fi
exec "$BIOFLOW_SERVER_PYTHON" deploy/server96.py "$@"
