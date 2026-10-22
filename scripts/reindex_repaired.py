"""Run inside the analysis Python environment after verified local-cache recovery."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'BioLLM_RawDataAnalysis'),str(ROOT/'.runtime/linux')]
from backend.app.config import Settings
from backend.app.models import TaskRepository
from backend.app.services.datasets import DatasetService

settings=Settings.from_env()
service=DatasetService(settings,TaskRepository(settings.database_path))
report=json.loads((ROOT/'runtime/link-repair.json').read_text())
rows=[]
for record in report['manifests']:
    did=service.register_manifest(Path(record['path']),source='uploaded',name=record['name'])
    detail=service.get(did)
    if detail['status']!='available':raise RuntimeError(detail['reason'])
    rows.append({**record,'dataset_id':did,'sample_count':detail['sample_count'],'status':detail['status']})
(ROOT/'runtime/linked-datasets.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
print(json.dumps([{k:r[k] for k in ('dataset_id','sample_count','status')} for r in rows]))
