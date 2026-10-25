"""Small update for an already uploaded full BioFlow package."""
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT=Path(__file__).resolve().parents[1]

def build():
    files=set()
    for folder in ('backend','frontend'):
        files.update(p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in {'.py','.js','.css','.png'} and '__pycache__' not in p.parts)
    for name in ('ai4s-interface.html','README.md','docs/RESEARCH_WORKSPACE.md','article_writing/frontend/session_store.py',
        'docs/superpowers/specs/2026-10-24-research-workspace-design.md','docs/superpowers/plans/2026-10-24-research-workspace.md',
        'BioLLM_RawDataAnalysis/backend/app/services/figure_contracts.py',
        'BioLLM_RawDataAnalysis/backend/app/services/figure_render.py',
        'BioLLM_RawDataAnalysis/backend/app/services/figure_worker.py',
        'BioLLM_RawDataAnalysis/workflow/bin/visualization/plot_utils.py'):
        files.add(ROOT/name)
    target=ROOT/'dist/BioFlow-research-update-20261024.tar.gz';target.parent.mkdir(exist_ok=True)
    prefix='BioFlow-research-update-20261024/';manifest=[]
    with tarfile.open(target,'w:gz') as tar:
        for path in sorted(files):
            content=path.read_bytes();relative=path.relative_to(ROOT).as_posix();info=tarfile.TarInfo(prefix+'payload/'+relative);info.size=len(content);info.mode=0o644;tar.addfile(info,io.BytesIO(content))
            manifest.append({'path':relative,'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()})
        for name,data in [('apply_research_update.py',(ROOT/'deploy/apply_research_update.py').read_bytes()),('UPDATE-MANIFEST.json',json.dumps({'version':'2026-10-24','files':manifest},ensure_ascii=False,indent=2).encode('utf-8')),('README.md',(ROOT/'docs/RESEARCH_WORKSPACE.md').read_bytes())]:
            info=tarfile.TarInfo(prefix+name);info.size=len(data);info.mode=0o644;tar.addfile(info,io.BytesIO(data))
    digest=hashlib.sha256(target.read_bytes()).hexdigest();Path(str(target)+'.sha256').write_text(digest+'  '+target.name+'\n')
    print(json.dumps({'package':str(target),'bytes':target.stat().st_size,'code_files':len(manifest),'sha256':digest}))

if __name__=='__main__':build()
