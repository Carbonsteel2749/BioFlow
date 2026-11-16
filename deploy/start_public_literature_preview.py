"""Start a second gateway, reusing live service URLs without restarting their supervisor."""
import os
from pathlib import Path
import subprocess
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def main():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8804/api/auth/me',timeout=2) as r:
            if r.status==200:print('preview already running');return
    except OSError:pass
    candidates=[]
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args=(proc/'cmdline').read_bytes().split(b'\0')
            if any(value in args for value in (b'backend.bioflow_platform.app:app',b'backend.bioflow_platform.skill_center_entrypoint:app')):candidates.append(proc)
        except OSError:continue
    if len(candidates)!=1:raise RuntimeError('Expected exactly one existing gateway')
    env=dict(line.split('=',1) for line in (candidates[0]/'environ').read_bytes().decode().split('\0') if '=' in line)
    env['PYTHONPATH']=str(ROOT)
    import sys
    sys.path.insert(0,str(ROOT))
    from deploy.start_literature_inference import ensure
    env.update(ensure())
    logs=ROOT/'runtime/logs';logs.mkdir(parents=True,exist_ok=True)
    with (logs/'public-literature-preview.log').open('ab') as log:
        child=subprocess.Popen(['/home/xh/BioLLM_RawDataAnalysis/.venv/bin/python','-m','uvicorn','deploy.public_literature_preview:app','--host','0.0.0.0','--port','8804'],cwd=ROOT,env=env,stdout=log,stderr=log,start_new_session=True)
    (ROOT/'runtime/public-literature-preview.pid').write_text(str(child.pid))
    for _ in range(40):
        if child.poll() is not None:raise RuntimeError('独立入口启动失败，请查看日志')
        try:
            with urllib.request.urlopen('http://127.0.0.1:8804/api/auth/me',timeout=1) as r:
                if r.status==200:break
        except OSError:time.sleep(.25)
    else:raise RuntimeError('独立入口尚未就绪，请查看日志')
    print('preview gateway started',child.pid)


if __name__=='__main__':main()
