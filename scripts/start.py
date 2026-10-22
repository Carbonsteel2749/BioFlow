"""Start the gateway and optional existing Python APIs, without their old UIs."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
local=ROOT/('.runtime/python' if sys.platform=='win32' else '.runtime/linux')
if local.is_dir():
    sys.path.insert(0,str(local))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--with-services',action='store_true',help='Also start local analysis and writing APIs')
    parser.add_argument('--port',type=int,default=8800)
    parser.add_argument('--wsl-distro',default='Ubuntu')
    args=parser.parse_args()
    children=[]
    env=os.environ.copy()
    env['PYTHONPATH']=os.pathsep.join([str(local),str(ROOT),env.get('PYTHONPATH','')])
    env.setdefault('ARTICLE_WRITING_ANALYSIS_ROOT',str(ROOT/'BioLLM_RawDataAnalysis/runtime/outputs'))
    logdir=ROOT/'runtime/logs'
    logdir.mkdir(parents=True,exist_ok=True)
    logs=[]
    def healthy(port):
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f'http://127.0.0.1:{port}/api/health',timeout=1) as r:
                return r.status==200
        except Exception:
            return False
    def start(name,cwd,target,port):
        url=f'http://127.0.0.1:{port}/api/health'
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url,timeout=1) as r:
                if r.status==200:
                    print(f'{name}: already running on {port}')
                    return
        except Exception:
            pass
        log=open(logdir/f'{name}.log','a',encoding='utf-8');logs.append(log)
        command=[sys.executable,'-m','uvicorn',target,'--host','127.0.0.1','--port',str(port)]
        if target.endswith(':create_app'):
            command.append('--factory')
        child=subprocess.Popen(command,cwd=cwd,env=env,stdout=log,stderr=log)
        children.append(child)
    try:
        if args.with_services:
            if not os.getenv('BIOFLOW_ANALYSIS_URL'):
                if healthy(8000):
                    print('analysis: already running on 8000')
                elif sys.platform=='win32':
                    linux_root='/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]
                    log=open(logdir/'analysis.log','a',encoding='utf-8');logs.append(log)
                    child=subprocess.Popen(['wsl','-d',args.wsl_distro,'--cd',linux_root,'--','python3','scripts/linux_analysis.py'],stdout=log,stderr=log)
                    children.append(child)
                else:
                    start('analysis',ROOT/'BioLLM_RawDataAnalysis','backend.app.main:create_app',8000)
            if not os.getenv('BIOFLOW_WRITING_URL'):
                start('writing',ROOT,'backend.bioflow_platform.writing_api:app',8765)
        print(f'BioFlow: http://127.0.0.1:{args.port}')
        print('Service logs: runtime/logs. Ctrl+C stops services started by this command.')
        import uvicorn
        uvicorn.run('backend.bioflow_platform.app:app',host='127.0.0.1',port=args.port)
    finally:
        for child in children:
            child.terminate()
        for child in children:
            try: child.wait(timeout=8)
            except subprocess.TimeoutExpired: child.kill()
        for log in logs: log.close()


if __name__=='__main__':
    main()
