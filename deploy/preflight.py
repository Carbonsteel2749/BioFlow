"""Read-only deployment evidence, without printing credentials or config values."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/('.runtime/python' if sys.platform=='win32' else '.runtime/linux')))
checks=[]
def check(name,ok,detail):
    checks.append({'name':name,'ok':bool(ok),'detail':detail})

check('Linux runtime',sys.platform.startswith('linux'),sys.platform)
check('Python 3.12',sys.version_info[:2]==(3,12),sys.version.split()[0])
for name in ['fastapi','uvicorn','httpx','pydantic','yaml','multipart','numpy','pandas','scipy','matplotlib','PIL']:
    check('Python: '+name,importlib.util.find_spec(name) is not None,'import availability')
for name in ['nextflow','java','fastqc','fastp','bowtie2','kraken2','bracken','humann','multiqc','soffice','docker']:
    check('Executable: '+name,shutil.which(name) is not None,'executable discovery')
root=ROOT/'BioLLM_RawDataAnalysis'
database=root/'runtime/state/tasks.sqlite3'
empty=[];missing=[];old_paths=0
if database.is_file():
    db=sqlite3.connect(f'file:{database.as_posix()}?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    for task in db.execute('SELECT id,manifest_path,result_archive FROM tasks'):
        old_paths+=int(not task['manifest_path'].startswith(str(root)))
        manifest=root/'runtime/incoming/manifests'/Path(task['manifest_path']).name
        if manifest.is_file():
            with manifest.open(encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    for key in ('read1','read2'):
                        raw=root/'runtime/incoming/manifests'/Path(row[key]).parent.name/Path(row[key]).name
                        if not raw.exists():missing.append(str(raw.relative_to(ROOT)))
                        elif raw.stat().st_size==0:empty.append(str(raw.relative_to(ROOT)))
        archive=root/'runtime/outputs'/task['id']/'deliverables'/Path(task['result_archive'] or '').name
        if not archive.is_file():missing.append(str(archive.relative_to(ROOT)))
        elif archive.stat().st_size==0:empty.append(str(archive.relative_to(ROOT)))
    db.close()
check('Research data content',not empty and not missing,{'empty_files':sorted(set(empty)),'missing_files':sorted(set(missing))})
check('Portable saved paths',old_paths==0,{'tasks_with_foreign_root':old_paths})
check('HTTPS session cookies',os.getenv('BIOFLOW_SECURE_COOKIE')=='1','BIOFLOW_SECURE_COOKIE=1 required behind HTTPS')
print(json.dumps({'ready':all(c['ok'] for c in checks),'checks':checks},ensure_ascii=False,indent=2))
