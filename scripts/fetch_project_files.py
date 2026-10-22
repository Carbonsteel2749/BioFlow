"""Copy the retained tasks' real files into BioFlow; never copy environments."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'BioLLM_RawDataAnalysis'
HOST='xh@219.224.3.96'
SOURCE='/home/xh/BioLLM_RawDataAnalysis'


def main():
    tasks=[json.loads(p.read_text(encoding='utf-8'))['task'] for p in (ROOT/'runtime/artifact-catalog').glob('*.json')]
    ids=[t['id'] for t in tasks]
    remote='''import csv,json,sqlite3
from pathlib import Path
root=Path(%r);ids=%r;files={};hashes={}
def add(p):
 p=Path(p)
 try:
  rel=p.resolve().relative_to(root.resolve()).as_posix()
  if p.is_file() and p.stat().st_size:files[rel]=p.stat().st_size
 except (OSError,ValueError):pass
db=sqlite3.connect(root/'runtime/state/tasks.sqlite3');db.row_factory=sqlite3.Row
for tid in ids:
 t=db.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
 if not t:continue
 if t['result_archive']:add(t['result_archive'])
 m=Path(t['manifest_path']);add(m)
 if m.is_file():
  with m.open() as f:
   for r in csv.DictReader(f):
    for k in ('read1','read2'):
     p=Path(r[k]);add(p if p.is_absolute() else m.parent/p)
 for r in db.execute('SELECT path,sha256 FROM artifacts WHERE task_id=? AND status="active"',(tid,)):
  add(r['path'])
  try:hashes[Path(r['path']).relative_to(root).as_posix()]=r['sha256']
  except ValueError:pass
print(json.dumps({'files':files,'hashes':hashes},ensure_ascii=False))
'''%(SOURCE,ids)
    response=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',HOST,'python3 -c '+shlex.quote(remote)],capture_output=True,check=True)
    inventory=json.loads(response.stdout.decode('utf-8'));files=inventory['files'];hashes=inventory['hashes'];needed=[]
    for name,size in files.items():
        path=TARGET/name
        # Portable CSVs deliberately differ from the source's absolute paths.
        if name.startswith('runtime/incoming/manifests/') and name.endswith('.csv') and path.is_file():continue
        different=not path.is_file() or path.stat().st_size!=size
        if not different and hashes.get(name):
            digest=hashlib.sha256()
            with path.open('rb') as handle:
                for block in iter(lambda:handle.read(8*1024*1024),b''):digest.update(block)
            different=digest.hexdigest()!=hashes[name]
        if different:needed.append((name,size))
    needed.sort(key=lambda r:r[1])
    report={'files':len(files),'copy_files':len(needed),'copy_bytes':sum(s for _,s in needed),'done':[],'failed':[]}
    report_path=ROOT/'runtime/project-file-transfer.json'
    print(json.dumps({k:v for k,v in report.items() if k not in {'done','failed'}}),flush=True)
    def copy(item):
        name,size=item;target=TARGET/name;target.parent.mkdir(parents=True,exist_ok=True)
        if os.name=='nt':target=Path('\\\\?\\'+str(target.resolve()))
        staging=ROOT/'runtime/transfer-parts';staging.mkdir(parents=True,exist_ok=True)
        temporary=staging/(hashlib.sha256(name.encode()).hexdigest()[:24]+'.part')
        result=subprocess.run(['scp','-p',HOST+':'+SOURCE+'/'+name,str(temporary)],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        if result.returncode or not temporary.is_file() or temporary.stat().st_size!=size:return {'path':name,'error':result.stderr.decode(errors='replace')[:300]}
        if target.is_file() and target.stat().st_size:
            backup=ROOT/'runtime/backups/project-files'/time.strftime('%Y%m%d-%H%M%S')/name;backup.parent.mkdir(parents=True,exist_ok=True)
            if os.name=='nt':backup=Path('\\\\?\\'+str(backup.resolve()))
            target.replace(backup)
        temporary.replace(target)
        return {'path':name,'size_bytes':size}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(copy,needed):
            report['failed' if 'error' in result else 'done'].append(result)
            report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            if len(report['done'])%20==0 or result['path'].endswith('.tar.gz'):print('copied',len(report['done']),result['path'],flush=True)
    print(json.dumps({'done':len(report['done']),'failed':len(report['failed'])}),flush=True)
    return bool(report['failed'])


if __name__=='__main__':sys.exit(main())
