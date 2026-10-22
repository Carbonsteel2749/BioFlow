"""Build a movable BioFlow bundle containing actual project files and databases."""
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
DIRS=['backend','frontend','bioflow_skills','scripts','deploy',
    'article_writing/article_writing','article_writing/frontend','article_writing/configs',
    'BioLLM_RawDataAnalysis/backend','BioLLM_RawDataAnalysis/workflow','BioLLM_RawDataAnalysis/config',
    'BioLLM_RawDataAnalysis/scripts','Article_repository/article_repository','Article_repository/scripts']
FILES=['README.md','ai4s-interface.html','Start-BioFlow.sh','requirements.txt','article_writing/pyproject.toml',
    'article_writing/requirements.txt','docs/FEATURE_DESIGN_REVIEW.md','docs/SERVER96_PACKAGE.md',
    'deploy/server96-inventory.json']


def selected():
    paths=set()
    for dirname in DIRS:
        for p in (ROOT/dirname).rglob('*'):
            if not p.is_file() or p.is_symlink():continue
            rel=p.relative_to(ROOT)
            if any(part in {'__pycache__','.pytest_cache','.git','.nextflow','node_modules','.venv','data','outputs','tests'} for part in rel.parts):continue
            if p.suffix in {'.pyc','.pyo','.db','.sqlite3'} or p.name.startswith('.env') or p.name=='bioflow.env':continue
            if str(rel).replace('\\','/').startswith('article_writing/frontend/static/'):continue
            if p.parent==ROOT/'BioLLM_RawDataAnalysis/workflow' and (p.suffix in {'.html','.log'} or p.name.startswith(('trace-','timeline-','.nextflow.log'))):continue
            paths.add(p)
    for name in FILES:
        p=ROOT/name
        if p.is_file():paths.add(p)
    for dirname in ['runtime/assets','runtime/artifact-catalog']:
        paths.update(p for p in (ROOT/dirname).rglob('*') if p.is_file() and not p.is_symlink())
    analysis=ROOT/'BioLLM_RawDataAnalysis'
    portable=analysis/'runtime/project-portable.json'
    data=json.loads(portable.read_text(encoding='utf-8'));paths.add(portable)
    for manifest in data['manifests']:
        paths.add(analysis/'runtime/incoming'/manifest['path'])
        for sample in manifest['samples']:
            for mate in ('read1','read2'):paths.add(analysis/'runtime/incoming'/sample[mate]['path'])
    for snapshot in (ROOT/'runtime/artifact-catalog').glob('*.json'):
        tid=json.loads(snapshot.read_text(encoding='utf-8'))['task']['id']
        paths.update(p for p in (analysis/'runtime/outputs'/tid).rglob('*') if p.is_file() and not p.name.endswith(('.part','.tmp')))
    paths.update(p for p in (analysis/'runtime/config').rglob('*') if p.is_file())
    paths.update(p for p in (ROOT/'article_writing/outputs/ui-sessions').rglob('*') if p.is_file())
    return sorted(paths)


class HashedReader:
    def __init__(self,stream):self.stream=stream;self.digest=hashlib.sha256()
    def read(self,size=-1):
        block=self.stream.read(size);self.digest.update(block);return block


def analysis_snapshot(destination):
    source=ROOT/'BioLLM_RawDataAnalysis/runtime/state/tasks.sqlite3'
    if sys.platform=='win32':
        def linux(path):return '/mnt/'+path.drive[0].lower()+path.as_posix()[2:]
        code='import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()'
        subprocess.run(['wsl','-d','Ubuntu','--','python3','-c',code,linux(source),linux(destination)],check=True)
    else:
        with sqlite3.connect(source) as src,sqlite3.connect(destination) as dst:src.backup(dst)


def build():
    output=ROOT/'dist';output.mkdir(exist_ok=True)
    target=output/'BioFlow.tar.gz'
    manifest=[]
    prefix='BioFlow/'
    with tempfile.TemporaryDirectory(dir=output,prefix='bundle-') as temporary:
        snapshot=Path(temporary)/'bioflow.sqlite3'
        with sqlite3.connect(ROOT/'runtime/bioflow.sqlite3') as source,sqlite3.connect(snapshot) as destination:
            source.backup(destination)
            destination.execute('DELETE FROM sessions');destination.execute('DELETE FROM login_attempts');destination.commit()
            assert destination.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        source.close();destination.close()
        analysis_db=Path(temporary)/'tasks.sqlite3';analysis_snapshot(analysis_db)
        with tarfile.open(target,'w:gz',compresslevel=1) as bundle:
            def add(path,relative):
                info=tarfile.TarInfo(prefix+relative);info.size=path.stat().st_size;info.mtime=int(time.time())
                info.mode=0o755 if path.suffix=='.sh' else 0o600 if relative.startswith('runtime/') else 0o644
                with path.open('rb') as stream:
                    reader=HashedReader(stream);bundle.addfile(info,reader)
                manifest.append({'path':relative,'bytes':info.size,'sha256':reader.digest.hexdigest()})
                if info.size>100000000:print(json.dumps({'included':relative,'bytes':info.size}),flush=True)
            for path in selected():add(path,path.relative_to(ROOT).as_posix())
            add(snapshot,'runtime/bioflow.sqlite3')
            add(analysis_db,'BioLLM_RawDataAnalysis/runtime/state/tasks.sqlite3')
            for dirname in ['article_writing/frontend/static','article_writing/outputs/ui-sessions','runtime/logs']:
                info=tarfile.TarInfo(prefix+dirname+'/');info.type=tarfile.DIRTYPE;info.mode=0o700;bundle.addfile(info)
            data=json.dumps({'profile':'xh@219.224.3.96','data_included':True,'files':manifest},ensure_ascii=False,indent=2).encode()
            info=tarfile.TarInfo(prefix+'MANIFEST.json');info.size=len(data);info.mode=0o644;bundle.addfile(info,io.BytesIO(data))
    hasher=hashlib.sha256()
    with target.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):hasher.update(block)
    digest=hasher.hexdigest()
    target.with_suffix(target.suffix+'.sha256').write_text(digest+'  '+target.name+'\n',encoding='utf-8')
    print(json.dumps({'package':str(target),'bytes':target.stat().st_size,'files':len(manifest),'sha256':digest},ensure_ascii=False))


if __name__=='__main__':build()
