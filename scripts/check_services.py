"""Read-only smoke check of the unified routes against running services."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.runtime/python'))
import httpx

paths=['/','/api/status','/api/skills','/services/analysis/api/tasks',
       '/services/analysis/api/workflows/templates','/services/analysis/api/workflows/registry',
       '/services/writing/api/sessions','/services/writing/api/upstream/analysis/packages',
       '/services/literature/health','/services/literature/api/knowledge-bases']
with httpx.Client(base_url='http://127.0.0.1:8800',trust_env=False,timeout=15) as client:
    for path in paths:
        try:
            response=client.get(path)
            print(json.dumps({'path':path,'status':response.status_code},ensure_ascii=False))
        except httpx.HTTPError as exc:
            print(json.dumps({'path':path,'error':type(exc).__name__}))
