"""One-command deployment on the verified xh@219.224.3.96 server.

Starts the packaged analysis service; reuses Python, reference databases, Ollama and literature.
No package installation, original database rewriting or old-service termination.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
RUNTIME=ROOT/'runtime'
PIDFILE=RUNTIME/'server96.pid'
ANALYSIS=Path(os.getenv('BIOFLOW_ANALYSIS_ROOT',str(ROOT/'BioLLM_RawDataAnalysis'))).resolve()
ANALYSIS_PORT=int(os.getenv('BIOFLOW_ANALYSIS_PORT','8803'))
OWN_ANALYSIS=not bool(os.getenv('BIOFLOW_ANALYSIS_URL'))
ANALYSIS_URL=os.getenv('BIOFLOW_ANALYSIS_URL','http://127.0.0.1:'+str(ANALYSIS_PORT)).rstrip('/')
LITERATURE_URL=os.getenv('BIOFLOW_LITERATURE_URL','http://127.0.0.1:8080').rstrip('/')
MODEL_URL=os.getenv('BIOFLOW_QWEN_URL','http://127.0.0.1:11434').rstrip('/')
WRITING_PORT=int(os.getenv('BIOFLOW_WRITING_PORT','8765'))
GATEWAY_PORT=int(os.getenv('BIOFLOW_PORT','8800'))
WRITING_URL='http://127.0.0.1:'+str(WRITING_PORT)
GATEWAY_URL='http://127.0.0.1:'+str(GATEWAY_PORT)


def fetch(url,timeout=4):
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url,timeout=timeout) as response:
        return json.load(response)


def healthy(url):
    try:fetch(url);return True
    except (OSError,ValueError):return False


def check():
    rows=[]
    def row(name,ok,detail):rows.append({'name':name,'ok':bool(ok),'detail':detail})
    row('平台 Python',sys.version_info>=(3,10),sys.executable)
    for module in ('fastapi','uvicorn','httpx','pydantic','yaml','multipart'):
        row(module,importlib.util.find_spec(module) is not None,'使用已有 Python 环境')
    row('分析输入', (ANALYSIS/'runtime/incoming').is_dir(),str(ANALYSIS/'runtime/incoming'))
    row('分析成果', (ANALYSIS/'runtime/outputs').is_dir(),str(ANALYSIS/'runtime/outputs'))
    if OWN_ANALYSIS:
        row('包内分析服务',(ANALYSIS/'backend/app/main.py').is_file(),str(ANALYSIS/'backend/app/main.py'))
        row('分析状态',(ANALYSIS/'runtime/state/tasks.sqlite3').is_file(),str(ANALYSIS/'runtime/state/tasks.sqlite3'))
    else:row('分析服务',healthy(ANALYSIS_URL+'/api/health'),ANALYSIS_URL)
    row('文献服务',healthy(LITERATURE_URL+'/health'),LITERATURE_URL)
    try:names=[m['name'] for m in fetch(MODEL_URL+'/api/tags').get('models',[])]
    except (OSError,ValueError):names=[]
    row('Qwen3-14B','qwen3:14b' in names,MODEL_URL)
    row('项目状态',(RUNTIME/'bioflow.sqlite3').is_file(),str(RUNTIME/'bioflow.sqlite3'))
    return {'ready':all(r['ok'] for r in rows),'checks':rows}


def is_supervisor(pid):
    try:
        args=Path('/proc')/str(pid)/'cmdline'
        command=args.read_bytes().replace(b'\0',b' ').decode(errors='replace')
        return str(ROOT/'deploy/server96.py') in command and 'supervise' in command
    except OSError:return False


def supervisor_pid():
    try:
        pid=int(PIDFILE.read_text().strip())
        return pid if is_supervisor(pid) else None
    except (OSError,ValueError):return None


def configure():
    os.environ.update({'BIOFLOW_ANALYSIS_ROOT':str(ANALYSIS),'BIOFLOW_ANALYSIS_URL':ANALYSIS_URL,
        'BIOFLOW_WRITING_URL':WRITING_URL,'BIOFLOW_LITERATURE_URL':LITERATURE_URL,
        'BIOFLOW_QWEN_URL':MODEL_URL,'BIOFLOW_DATABASE':str(RUNTIME/'bioflow.sqlite3'),
        'ARTICLE_WRITING_ANALYSIS_ROOT':str(ANALYSIS/'runtime/outputs')})
    # Dataset identifiers travel with the packaged databases and stay stable.
    backup=RUNTIME/'backups'/('startup-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.sqlite3')
    backup.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(RUNTIME/'bioflow.sqlite3') as source,sqlite3.connect(backup) as target:source.backup(target)
    from backend.bioflow_platform.store import Store
    store=Store(RUNTIME/'bioflow.sqlite3')
    with store.connect() as db:
        db.execute('UPDATE catalogue_sync SET updated_at=0')
        for row in db.execute('SELECT id,user_id,project_id,path FROM assets').fetchall():
            name=Path(row['path'].replace('\\','/')).name
            relocated=RUNTIME/'assets'/row['user_id']/row['project_id']/name
            if relocated.is_file():db.execute('UPDATE assets SET path=? WHERE id=?',(relocated.relative_to(RUNTIME/'assets').as_posix(),row['id']))
    config=ROOT/'article_writing/outputs/ui-config/llm.json'
    config.parent.mkdir(parents=True,exist_ok=True)
    # Preserve deliberate user changes after the first deployment.
    if not config.exists():
        config.write_text(json.dumps({'enabled':True,'provider':'ollama','model':'qwen3:14b','url':MODEL_URL,
            'timeout':300,'language':'en+zh'},ensure_ascii=False,indent=2),encoding='utf-8')


def wait_ready(child,url,seconds=45):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if child.poll() is not None:raise RuntimeError('服务启动失败，请查看 runtime/logs/server96.log')
        if healthy(url):return
        time.sleep(.5)
    raise RuntimeError('服务启动超时：'+url)


def supervise():
    import fcntl
    RUNTIME.mkdir(exist_ok=True)
    with (RUNTIME/'server96.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('本目录的 BioFlow 已在运行')
        report=check()
        if not report['ready']:raise RuntimeError(json.dumps(report,ensure_ascii=False))
        if healthy(GATEWAY_URL+'/api/auth/me'):raise RuntimeError('统一入口端口已被其他实例占用')
        configure()
        PIDFILE.write_text(str(os.getpid()))
        children=[];stopping=False
        def stop(signum,frame):
            nonlocal stopping
            stopping=True
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        env=os.environ.copy();env['PYTHONPATH']=str(ROOT)
        try:
            if OWN_ANALYSIS:
                if healthy(ANALYSIS_URL+'/api/health'):raise RuntimeError('包内分析端口已被其他实例占用')
                analysis_env=env.copy()
                analysis_env.update({'PYTHONPATH':str(ANALYSIS),'BIOLLM_INPUT_ROOT':str(ANALYSIS/'runtime/incoming'),
                    'BIOLLM_STATE_ROOT':str(ANALYSIS/'runtime'),'BIOLLM_WORKFLOW_PATH':str(ANALYSIS/'workflow/main.nf'),
                    'BIOLLM_DATABASE_REGISTRY':str(ANALYSIS/'config/database-registry.local.json'),
                    'BIOLLM_DATABASE_PROFILE':'server-v1','BIOLLM_DATABASE_MANIFEST':str(ANALYSIS/'runtime/config/database.resolved.json')})
                for key,value in {'BIOLLM_HOST_INDEX':'/home/xh/databases/host/GRCh38_noalt/index/GRCh38_noalt_as',
                    'BIOLLM_NEXTFLOW_BIN':'/home/xh/.local/bin/nextflow'}.items():analysis_env.setdefault(key,value)
                child=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:create_app','--factory','--host','127.0.0.1','--port',str(ANALYSIS_PORT)],cwd=ANALYSIS,env=analysis_env)
                children.append(child);wait_ready(child,ANALYSIS_URL+'/api/health',seconds=90)
            if not healthy(WRITING_URL+'/api/health'):
                child=subprocess.Popen([sys.executable,'-m','uvicorn','backend.bioflow_platform.writing_api:app','--host','127.0.0.1','--port',str(WRITING_PORT)],cwd=ROOT,env=env)
                children.append(child);wait_ready(child,WRITING_URL+'/api/health')
            child=subprocess.Popen([sys.executable,'-m','uvicorn','backend.bioflow_platform.app:app','--host',os.getenv('BIOFLOW_BIND_HOST','0.0.0.0'),'--port',str(GATEWAY_PORT)],cwd=ROOT,env=env)
            children.append(child);wait_ready(child,GATEWAY_URL+'/api/auth/me')
            print('BioFlow ready: http://219.224.3.96:'+str(GATEWAY_PORT)+'/',flush=True)
            while not stopping:
                if any(p.poll() is not None for p in children):raise RuntimeError('服务进程已退出，请查看运行日志')
                time.sleep(1)
        finally:
            for child in children:
                if child.poll() is None:child.terminate()
            for child in children:
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:child.kill();child.wait()
            PIDFILE.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description='BioFlow original-server deployment')
    parser.add_argument('action',nargs='?',choices=['start','stop','status','check','supervise'],default='start')
    args=parser.parse_args()
    if args.action=='check':
        report=check();print(json.dumps(report,ensure_ascii=False,indent=2));return 0 if report['ready'] else 1
    if args.action=='supervise':supervise();return 0
    pid=supervisor_pid()
    if args.action=='status':
        print(json.dumps({'running':bool(pid),'pid':pid,'gateway':GATEWAY_URL,'analysis':ANALYSIS_URL,'model':MODEL_URL},ensure_ascii=False));return 0
    if args.action=='stop':
        if pid:os.kill(pid,signal.SIGTERM);print('已通知本目录的服务停止，原分析、文献和模型服务继续运行')
        else:print('本目录的 BioFlow 未运行')
        return 0
    if pid:print('BioFlow 已运行，PID '+str(pid));return 0
    report=check()
    if not report['ready']:print(json.dumps(report,ensure_ascii=False,indent=2));return 1
    RUNTIME.mkdir(exist_ok=True);log=RUNTIME/'logs/server96.log';log.parent.mkdir(exist_ok=True)
    with log.open('ab',buffering=0) as output:
        child=subprocess.Popen([sys.executable,str(ROOT/'deploy/server96.py'),'supervise'],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
    wait_ready(child,GATEWAY_URL+'/api/auth/me',seconds=120)
    print('已启动：http://219.224.3.96:'+str(GATEWAY_PORT)+'/')
    print('账户 developer；密码 developer。日志：'+str(log))
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (RuntimeError,OSError,ValueError) as exc:print(str(exc),file=sys.stderr);sys.exit(1)
