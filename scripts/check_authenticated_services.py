"""Local administrator smoke check; ephemeral test session is revoked afterwards."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/('.runtime/python' if sys.platform=='win32' else '.runtime/linux'))]
import httpx
from backend.bioflow_platform.store import Store
from backend.bioflow_platform import auth

store=Store(ROOT/'runtime/bioflow.sqlite3')
with store.connect() as db:
    user=db.execute("SELECT id FROM users WHERE role='developer' LIMIT 1").fetchone()
if not user:
    raise SystemExit('No developer account in this workspace')
token,csrf=auth.new_session(store,user['id'])
try:
    with httpx.Client(base_url='http://127.0.0.1:8800',trust_env=False,timeout=20,headers={'Cookie':auth.COOKIE+'='+token,'X-BioFlow-CSRF':csrf}) as client:
        for path in ['/api/projects','/api/models','/api/skills','/api/status','/services/analysis/api/tasks','/services/analysis/api/datasets','/services/writing/api/sessions','/services/writing/api/upstream/analysis/packages']:
            r=client.get(path)
            data=r.json()
            record={'path':path,'status':r.status_code}
            if isinstance(data,list):record['count']=len(data)
            elif isinstance(data,dict):
                for key in ('items','sessions','packages'):
                    if isinstance(data.get(key),list):record['count']=len(data[key]);break
            if path.endswith('/api/tasks') and isinstance(data,list):record['files']=[t.get('file_availability') for t in data]
            print(json.dumps(record,ensure_ascii=False))
finally:
    auth.logout(store,token)
