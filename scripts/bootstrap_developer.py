"""One-time account/record migration requested by the workspace owner."""
import argparse
import csv
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/('.runtime/python' if os.name=='nt' else '.runtime/linux'))]
from backend.bioflow_platform.store import Store
from backend.bioflow_platform import auth


def inside(path,parent):
    path=path.resolve()
    if not path.is_relative_to(parent.resolve()):
        raise ValueError('Migration target is outside the workspace')
    return path


def migrate(analysis_root=None):
    if (ROOT/'runtime/migration-latest.json').exists():
        raise RuntimeError('This workspace was already migrated; use the saved migration report')
    store=Store(ROOT/'runtime/bioflow.sqlite3')
    with store.connect() as db:
        existing=db.execute("SELECT id,username FROM users WHERE role='developer' LIMIT 1").fetchone()
    password=None
    if existing:
        uid,username=existing['id'],existing['username']
        password=os.getenv('BIOFLOW_DEVELOPER_PASSWORD') or secrets.token_urlsafe(18)
        with store.connect() as db:
            db.execute('UPDATE users SET password_hash=? WHERE id=?',(auth.hash_password(password),uid))
    else:
        username=os.getenv('BIOFLOW_DEVELOPER_USERNAME','developer')
        password=os.getenv('BIOFLOW_DEVELOPER_PASSWORD') or secrets.token_urlsafe(18)
        uid=auth.create_user(store,username,password,'developer')['id']
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    archive=ROOT/'runtime/archive'/stamp
    archive.mkdir(parents=True,exist_ok=True)
    source=ROOT/'BioLLM_RawDataAnalysis/runtime/state/tasks.sqlite3'
    connection=sqlite3.connect(source);connection.row_factory=sqlite3.Row
    snapshot=sqlite3.connect(archive/'analysis-before.sqlite3')
    connection.backup(snapshot);snapshot.close()
    with store.connect() as src, sqlite3.connect(archive/'workspace-before.sqlite3') as target:
        src.backup(target)
    tasks=[dict(r) for r in connection.execute('SELECT * FROM tasks ORDER BY created_at DESC')]
    keep=[];discard=[]
    for task in tasks:
        manifest=ROOT/'BioLLM_RawDataAnalysis/runtime/incoming/manifests'/Path(task['manifest_path']).name
        samples=[]
        if manifest.is_file():
            with manifest.open(encoding='utf-8-sig') as handle:
                samples=[r.get('sample_id','') for r in csv.DictReader(handle)]
        task['sample_ids']=samples
        (keep if any(re.search(r'(?:SRR|ERR)\d+',name) for name in samples) else discard).append(task)
    if not keep:
        raise RuntimeError('No SRR/ERR records found; migration stopped')
    old_ids=[t['id'] for t in discard]
    if old_ids:
        placeholders=','.join('?' for _ in old_ids)
        artifact_ids=[r[0] for r in connection.execute(f'SELECT artifact_id FROM artifacts WHERE task_id IN ({placeholders})',old_ids)]
        tables=[r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'") if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',r[0])]
        with connection:
            for table in tables:
                columns={r[1] for r in connection.execute(f'PRAGMA table_info("{table}")')}
                if artifact_ids and table=='artifact_relations':
                    for col in columns:
                        if col.endswith('artifact_id'):
                            connection.execute(f'DELETE FROM "{table}" WHERE "{col}" IN ({",".join("?" for _ in artifact_ids)})',artifact_ids)
                if 'task_id' in columns:
                    connection.execute(f'DELETE FROM "{table}" WHERE task_id IN ({placeholders})',old_ids)
            connection.execute(f'DELETE FROM tasks WHERE id IN ({placeholders})',old_ids)
    target_root=analysis_root or ('/mnt/'+ROOT.drive[0].lower()+ROOT.as_posix()[2:]+'/BioLLM_RawDataAnalysis' if os.name=='nt' else str(ROOT/'BioLLM_RawDataAnalysis'))
    oldroot='/home/xh/BioLLM_RawDataAnalysis'
    with connection:
        for table, in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',table):continue
            for col in connection.execute(f'PRAGMA table_info("{table}")').fetchall():
                if col[2].upper()=='TEXT':
                    if table=='datasets' and col[1]=='manifest_path':
                        connection.execute('UPDATE datasets SET manifest_path=replace(manifest_path,?,?) WHERE manifest_path LIKE ? AND NOT EXISTS(SELECT 1 FROM datasets other WHERE other.manifest_path=replace(datasets.manifest_path,?,?))',(oldroot,target_root,'%'+oldroot+'%',oldroot,target_root))
                        continue
                    connection.execute(f'UPDATE "{table}" SET "{col[1]}"=replace("{col[1]}",?,?) WHERE "{col[1]}" LIKE ?', (oldroot,target_root,'%'+oldroot+'%'))
    datasets=[dict(r) for r in connection.execute('SELECT * FROM datasets')]
    artifacts=[dict(r) for r in connection.execute('SELECT artifact_id,task_id FROM artifacts')]
    connection.close()
    with store.connect() as db:
        db.execute('UPDATE projects SET archived=1')
        db.execute('UPDATE resources SET archived=1')
    p=store.create('SRR / ERR 宏基因组研究','已保留全部含 SRR 或 ERR 编号的历史分析任务',uid)
    for task in keep:
        store.claim('task',task['id'],uid)
        store.link(p['id'],'task',task['id'])
    kept_names={Path(t['manifest_path']).name for t in keep}
    for data in datasets:
        if Path(data['manifest_path']).name in kept_names and data['manifest_path'].startswith(target_root):
            store.claim('dataset',data['id'],uid,data)
    sessions=ROOT/'article_writing/outputs/ui-sessions'
    moved=[]
    if sessions.exists():
        for directory in sessions.iterdir():
            if directory.is_dir():
                target=archive/'writing-sessions'/directory.name
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.move(str(inside(directory,ROOT)),str(inside(target,ROOT)))
                moved.append(directory.name)
    for task in discard:
        directory=ROOT/'BioLLM_RawDataAnalysis/runtime/outputs'/task['id']
        if directory.is_dir():
            target=archive/'analysis-outputs'/task['id']
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(str(inside(directory,ROOT)),str(inside(target,ROOT)))
    report={'username':username,'project_id':p['id'],'kept_tasks':[{'id':t['id'],'name':t['name'],'sample_ids':t['sample_ids']} for t in keep],
        'archived_task_ids':old_ids,'archived_writing_sessions':moved,'analysis_root':target_root,'backup':str(archive)}
    (archive/'migration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'runtime/migration-latest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'username':username,'password':password,'kept_tasks':len(keep),'archived_tasks':len(old_ids),'archived_manuscripts':len(moved),'project_id':p['id']},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--analysis-root')
    args=parser.parse_args()
    migrate(args.analysis_root)
