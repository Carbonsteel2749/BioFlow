"""Single leased worker, durable events, cancellable steps and verified resume."""
import argparse
import json
import mimetypes
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from . import assets
from . import research_store as rs
from .store import Store

def state(store,row):return rs.get(store,row['project_id'],row['user_id'],row['id'],'run')

def save_active(store,row,**changes):
    # A late step completion must never overwrite a cancellation request.
    current=state(store,row)
    payload={k:v for k,v in current.items() if k not in {'id','kind','project_id','user_id','status','created_at','updated_at','contract_version'}}
    payload.update(changes)
    with store.connect() as db:
        db.execute('UPDATE research_records SET payload=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(rs.dump(payload),row['id']))
    return state(store,row)

def publish(store,row,output,directory):
    uid,pid=row['user_id'],row['project_id'];artifacts=[];mapping={}
    for artifact in output.get('artifacts',[]):
        source=(directory/artifact['storage']).resolve()
        if not source.is_relative_to(directory.resolve()) or not source.is_file() or rs.digest(source)!=artifact['sha256']:raise ValueError('工具产物路径或内容校验失败')
        identifier=uuid.uuid4().hex;stored=identifier+source.suffix;target=assets.ROOT/'runtime/assets'/uid/pid/stored;target.parent.mkdir(parents=True,exist_ok=True)
        try:os.link(source,target)
        except OSError:__import__('shutil').copyfile(source,target)
        kind='charts' if source.suffix in {'.png','.svg','.pdf'} else 'results'
        payload={'run_id':row['id'],'step_id':output['step_id'],'filename':source.name,'stored_name':stored,'sha256':artifact['sha256'],
                 'size_bytes':target.stat().st_size,'artifact_type':artifact['artifact_type'],'output_key':artifact.get('output_key'),
                 'title':artifact.get('title',source.name),'source_dataset_ids':row['plan']['dataset_ids']}
        saved=rs.create(store,pid,uid,'artifact','ready',payload,identifier);mapping[artifact['id']]=identifier
        metadata={'task_id':row.get('task_id') or row['id'],'source':'BioFlow统一研究','research_run_id':row['id'],'research_step_id':output['step_id'],
                  'research_artifact_id':identifier,'chart_role':'statistical_result' if kind=='results' else 'figure',
                  'parent_refs':[rs.get(store,pid,uid,d,'dataset')['source_asset_id'] for d in row['plan']['dataset_ids']],'process_name':output['skill']}
        if row.get('task_id'):
            task=rs.get(store,pid,uid,row['task_id'],'task')
            if task.get('legacy_id'):metadata['task_id']=task['legacy_id']
        with store.connect() as db:
            db.execute('INSERT INTO assets(id,project_id,user_id,name,kind,path,size_bytes,sha256,status,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (identifier,pid,uid,source.name,kind,str(target),payload['size_bytes'],payload['sha256'],'ready',rs.dump(metadata)))
        artifacts.append(rs.public(saved))
    evidence=json.loads(rs.dump(output.get('evidence',[])))
    for item in evidence:item['artifact_ids']=[mapping.get(a,a) for a in item.get('artifact_ids',[])]
    return {'tool_id':output['skill'],'tool_version':output.get('version','1.0.0'),'status':output['status'],
            'summary':output.get('summary',''),'method':output.get('method',''),'metrics':output.get('metrics',{}),
            'warnings':output.get('warnings',[]),'evidence':evidence,'artifacts':artifacts,'step_id':output['step_id'],
            'input_snapshot':row['plan']['snapshot'],'plan_fingerprint':row['plan']['fingerprint']}

def result_valid(result):
    for artifact in result.get('artifacts',[]):
        path=assets.ROOT/'runtime/assets'/artifact['user_id']/artifact['project_id']/artifact['stored_name']
        if not path.is_file() or rs.digest(path)!=artifact['sha256']:return False
    return True

def process(store,row):
    directory=rs.root()/row['user_id']/row['project_id']/'runs'/row['id'];directory.mkdir(parents=True,exist_ok=True)
    datasets=[rs.get(store,row['project_id'],row['user_id'],d,'dataset') for d in row['plan']['dataset_ids']]
    for d in datasets:d['directory']=str(rs.verify_dataset(d))
    steps=row['plan']['steps'];completed=[];results=[];resume=True
    for index,step in enumerate(steps):
        current=state(store,row)
        if current['status']=='cancelling':rs.update(store,current,'cancelled');rs.event(store,row,'cancelled');return
        previous=next((x for x in row.get('step_results',[]) if x['step_id']==step['id'] and x['status']=='success'),None)
        if resume and previous and result_valid(previous):
            results.append(previous);completed.append(previous['raw_engine_result']);rs.event(store,row,'step_reused',step_id=step['id']);continue
        resume=False
        if any(not any(c['step_id']==dep and c['status']=='success' for c in completed) for dep in step.get('depends_on',[])):
            blocked={'step_id':step['id'],'status':'skipped','summary':'上游步骤未成功，未执行此步骤。','artifacts':[]}
            results.append(blocked);completed.append(blocked);rs.event(store,row,'step_blocked',step_id=step['id']);continue
        spec={'mode':'execute','project_id':row['project_id'],'run_id':row['id'],'datasets':datasets,
              'step':step,'study':row['plan']['study'],'completed':completed}
        req=directory/'step.request.json';response=directory/'step.response.json';req.write_text(rs.dump(spec));response.unlink(missing_ok=True)
        rs.event(store,row,'step_started',step_id=step['id'],tool_id=step['skill']);save_active(store,row,current_step=step['id'],step_results=results,progress={'completed':index,'total':len(steps)})
        with (directory/'execution.log').open('ab') as log:
            proc=subprocess.Popen([rs.python(),'-m','backend.bioflow_platform.research_engine_bridge',str(req),str(response)],
                cwd=Path(__file__).resolve().parents[2],env=rs.engine_env(directory),stdout=log,stderr=log,start_new_session=True)
        started=time.monotonic()
        try:
            while proc.poll() is None:
                if state(store,row)['status']=='cancelling':raise InterruptedError('用户取消')
                if time.monotonic()-started>int(os.environ.get('BIOFLOW_RESEARCH_STEP_TIMEOUT','600')):raise TimeoutError('步骤超过运行时限')
                time.sleep(.3)
        except BaseException:
            if proc.poll() is None:
                os.killpg(proc.pid,signal.SIGTERM)
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            raise
        if not response.exists():raise ValueError('工具未生成结果，请检查执行日志')
        output=json.loads(response.read_text())
        if proc.returncode or output.get('error'):raise ValueError(output.get('error','步骤执行失败'))
        output['step_id']=step['id'];normalized=publish(store,row,output,directory)
        result=rs.create(store,row['project_id'],row['user_id'],'result',output['status'],normalized)
        entry={**rs.public(result),'raw_engine_result':output};results.append(entry);completed.append(output)
        save_active(store,row,step_results=results,progress={'completed':index+1,'total':len(steps)})
        rs.event(store,row,'step_completed',step_id=step['id'],status=output['status'],result_id=result['id'])
    current=save_active(store,row,step_results=results,current_step=None,progress={'completed':len(steps),'total':len(steps)})
    from .research_experience import build_report
    current=save_active(store,row,report=build_report(current,use_ai=False))
    intended='completed' if all(r['status']=='success' for r in results) else 'completed_with_issues'
    with store.connect() as db:
        db.execute("UPDATE research_records SET status=CASE WHEN status='cancelling' THEN 'cancelled' ELSE ? END,updated_at=CURRENT_TIMESTAMP WHERE id=?",(intended,row['id']))
    status=state(store,row)['status']
    rs.event(store,row,status)

def run_one(store):
    rs.initialize(store)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        item=db.execute("SELECT * FROM research_records WHERE kind='run' AND status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if not item:return False
        db.execute("UPDATE research_records SET status='running',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'",(item['id'],))
    row=state(store,rs.record(item));rs.event(store,row,'started')
    try:process(store,row)
    except Exception as exc:
        current=state(store,row);status='cancelled' if current['status']=='cancelling' or isinstance(exc,InterruptedError) else 'failed'
        rs.update(store,current,status,error=str(exc)[:1800]);rs.event(store,row,status,message=str(exc)[:1800])
    return True

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--database',required=True);args=parser.parse_args()
    assets.ROOT=Path(os.environ.get('BIOFLOW_RESEARCH_ROOT',str(assets.ROOT)));store=Store(args.database);rs.initialize(store)
    import fcntl
    rs.root().mkdir(parents=True,exist_ok=True)
    lock=(rs.root()/(rs.fingerprint(str(store.path))[:16]+'.lock')).open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    def shutdown(signum,frame):raise SystemExit(0)
    signal.signal(signal.SIGTERM,shutdown)
    with store.connect() as db:
        db.execute("UPDATE research_records SET status='interrupted',updated_at=CURRENT_TIMESTAMP WHERE kind='run' AND status='running'")
        db.execute("UPDATE research_records SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE kind='run' AND status='cancelling'")
    idle=0
    while idle<300:
        if run_one(store):idle=0
        else:idle+=1;time.sleep(1)

if __name__=='__main__':main()
