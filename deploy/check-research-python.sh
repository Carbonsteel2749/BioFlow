#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
research_python="${BIOFLOW_RESEARCH_PYTHON:-$PWD/runtime/research-python/bin/python}"
PYTHONPATH="$PWD/backend/bioflow_platform/research_engine/src" "$research_python" - <<'PY'
import json
from yanji.linux_sandbox import capability
result = capability()
print(json.dumps(result, ensure_ascii=False))
if not result['available']:
    raise SystemExit(1)
import numpy, pandas, scipy, matplotlib, statsmodels, skbio
print('BioFlow 通用 Python 隔离执行与科学计算依赖自检通过。')
PY
