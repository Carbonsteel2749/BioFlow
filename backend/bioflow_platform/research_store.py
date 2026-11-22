"""Authoritative project records; engine databases are disposable execution state."""
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from . import assets
from .research_contracts import CONTRACT_VERSION

def dump(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def fingerprint(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()

def initialize(store):
    with store.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS research_records(
          id TEXT PRIMARY KEY,kind TEXT NOT NULL,project_id TEXT NOT NULL,user_id TEXT NOT NULL,
          status TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS research_owned ON research_records(project_id,user_id,kind);
        CREATE TABLE IF NOT EXISTS research_events(
          id INTEGER PRIMARY KEY,run_id TEXT NOT NULL,project_id TEXT NOT NULL,user_id TEXT NOT NULL,
          event TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        ''')

def record(row):
    row=dict(row);payload=json.loads(row.pop('payload'))
    return {**payload,**row,'contract_version':CONTRACT_VERSION}

def get(store,pid,uid,identifier,kind=None):
    initialize(store)
    with store.connect() as db:
        row=db.execute('SELECT * FROM research_records WHERE id=? AND project_id=? AND user_id=?',
                       (identifier,pid,uid)).fetchone()
    if not row or (kind and row['kind']!=kind):raise KeyError('研究记录不存在')
    return record(row)

def listing(store,pid,uid,kind):
    initialize(store)
    with store.connect() as db:
        rows=db.execute('SELECT * FROM research_records WHERE project_id=? AND user_id=? AND kind=? ORDER BY created_at,id',
                        (pid,uid,kind)).fetchall()
    return [record(row) for row in rows]

def create(store,pid,uid,kind,status,payload,identifier=None):
    initialize(store);identifier=identifier or uuid.uuid4().hex
    with store.connect() as db:
        db.execute('INSERT INTO research_records(id,kind,project_id,user_id,status,payload) VALUES(?,?,?,?,?,?)',
                   (identifier,kind,pid,uid,status,dump(payload)))
    return get(store,pid,uid,identifier,kind)

def update(store,row,status,**changes):
    old=get(store,row['project_id'],row['user_id'],row['id'])
    payload={k:v for k,v in old.items() if k not in {'id','kind','project_id','user_id','status','created_at','updated_at','contract_version'}}
    payload.update(changes)
    with store.connect() as db:
        db.execute('UPDATE research_records SET status=?,payload=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND project_id=? AND user_id=?',
                   (status,dump(payload),row['id'],row['project_id'],row['user_id']))
    return get(store,row['project_id'],row['user_id'],row['id'])

def event(store,row,name,**payload):
    with store.connect() as db:
        db.execute('INSERT INTO research_events(run_id,project_id,user_id,event,payload) VALUES(?,?,?,?,?)',
                   (row['id'],row['project_id'],row['user_id'],name,dump(payload)))

def root():
    return assets.ROOT/'runtime/research'

def python():
    return os.environ.get('BIOFLOW_RESEARCH_PYTHON',str(assets.ROOT/'runtime/research-python/bin/python'))

def engine_env(directory):
    env=os.environ.copy();env['YANJI_RUNTIME']=str(directory)
    env['YANJI_BASE_URL']=os.environ.get('BIOFLOW_RESEARCH_MODEL_URL','')
    env['YANJI_MODEL']=os.environ.get('BIOFLOW_RESEARCH_MODEL','qwen3:14b')
    env['YANJI_API_KEY']=os.environ.get('BIOFLOW_RESEARCH_MODEL_KEY','local' if env['YANJI_BASE_URL'].startswith('http://127.0.0.1:') else '')
    env['MPLCONFIGDIR']=str(Path(directory)/'matplotlib')
    env['PYTHONPATH']=str(Path(__file__).resolve().parents[2])
    return env

def invoke(spec,directory,timeout=240):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    identifier=uuid.uuid4().hex;request=directory/(identifier+'.request.json');response=directory/(identifier+'.response.json')
    request.write_text(dump(spec),encoding='utf-8')
    try:
        completed=subprocess.run([python(),'-m','backend.bioflow_platform.research_engine_bridge',str(request),str(response)],
          env=engine_env(directory),cwd=Path(__file__).resolve().parents[2],capture_output=True,timeout=timeout)
        if not response.exists():raise ValueError('统计引擎未就绪，请检查独立计算环境及运行日志')
        result=json.loads(response.read_text())
        if completed.returncode or result.get('error'):raise ValueError(result.get('error','统计引擎执行失败'))
        return result
    except subprocess.TimeoutExpired:raise ValueError('请求超过执行时限，已停止本次子进程') from None
    finally:
        request.unlink(missing_ok=True);response.unlink(missing_ok=True)

def public(row):
    private={'directory','storage','engine_snapshot','raw_engine_result','payload_path'}
    if isinstance(row,dict):return {k:public(v) for k,v in row.items() if k not in private}
    if isinstance(row,list):return [public(v) for v in row]
    return row

def verify_dataset(dataset):
    directory=root()/dataset['user_id']/dataset['project_id']/'data'/dataset['id']
    for name,expected in dataset['hashes'].items():
        path=directory/name
        if not path.is_file() or digest(path)!=expected:raise ValueError('数据版本内容已变化，请重新导入后生成新方案')
    return directory
