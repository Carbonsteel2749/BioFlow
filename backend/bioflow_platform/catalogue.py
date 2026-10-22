"""A persistent metadata index. File bytes are opened only by detail/download routes."""
import asyncio
import json
import time
from . import assets

JOBS={}


def put(store,pid,uid,collection,rows):
    with store.connect() as db:
        db.execute('DELETE FROM catalogue_entries WHERE project_id=? AND user_id=? AND collection=?',(pid,uid,collection))
        db.executemany('INSERT OR REPLACE INTO catalogue_entries VALUES(?,?,?,?,?)',[(pid,uid,r['id'],collection,json.dumps(r,ensure_ascii=False)) for r in rows])


def task_rows(store,pid,uid):
    tids=set(store.ids(pid,'task'))&store.owned_ids('task',uid)
    rows=assets.existing_outputs(tids)
    for row in rows:
        if row['status'] in {'empty','missing','integrity_error'}:row['url']=None;continue
        if row.get('url'):row['url']=row['url'].replace('{pid}',pid)
        if row.get('artifact_id'):row['url']=f"/api/projects/{pid}/artifacts/{row['artifact_id']}/preview?download=1"
        elif row.get('relative_path'):row['url']=f"/api/projects/{pid}/catalogue/{row['id']}/download"
    return rows


async def refresh(service,pid,user,key):
    store=service.get_store();uid=user['id'];errors=[]
    async def fetch(kind,path):
        return await asyncio.wait_for(service.call(kind,path),timeout=8)
    try:
        tids=set(store.ids(pid,'task'))&store.owned_ids('task',uid)
        semaphore=asyncio.Semaphore(3)
        async def task(tid):
            async with semaphore:
                try:
                    data,records=await asyncio.gather(fetch('analysis','/api/tasks/'+tid),fetch('analysis','/api/tasks/'+tid+'/artifacts'))
                    previous={a['artifact_id']:a for a in assets.read_snapshot(tid).get('artifacts',[])}
                    for record in records:
                        old=previous.get(record['artifact_id'],{})
                        if not record.get('path') and old.get('path'):record['path']=old['path']
                        path=assets.local_output(record.get('path',''))
                        if path is not None:
                            try:
                                record['local_size']=path.stat().st_size
                                record['local_status']='empty' if not record['local_size'] else 'ready' if old.get('local_status')=='ready' and old.get('sha256')==record.get('sha256') else 'not_checked'
                            except OSError:record['local_size']=0;record['local_status']='missing'
                    # Keep registered relative locations; discovery/hashing is deferred to opening a file.
                    await asyncio.to_thread(assets.write_snapshot,tid,data,records)
                except Exception:errors.append('部分任务目录暂未同步')
        await asyncio.gather(*(task(tid) for tid in tids))
        await asyncio.to_thread(put,store,pid,uid,'tasks',task_rows(store,pid,uid))
        async def dataset(did):
            async with semaphore:
                try:
                    from .project_files import dataset_files
                    try:d=await asyncio.to_thread(dataset_files,store,pid,uid,did)
                    except Exception:d=await fetch('analysis','/api/datasets/'+did)
                    title=next((t.get('name') for t in d.get('tasks',[]) if t.get('name')),None) or d.get('name') or '历史测序数据集'
                    return {'id':'dataset:'+did,'dataset_id':did,'kind':'datasets','name':title,
                            'sample_count':d.get('sample_count'),'sample_ids':[s['sample_id'] for s in d.get('samples',[])],
                            'status':d.get('status','not_checked'),'reason':d.get('reason'),'source':'项目数据集'}
                except Exception:errors.append('部分数据集目录暂未同步');return None
        dids=set(store.ids(pid,'dataset'))&store.owned_ids('dataset',uid)
        datasets=await asyncio.gather(*(dataset(did) for did in dids))
        if datasets and all(d is not None for d in datasets):put(store,pid,uid,'datasets',datasets)
        kb=store.get(pid)['knowledge_base_id']
        if kb:
            try:
                result=await fetch('literature','/api/knowledge-bases/'+kb+'/documents')
                docs=result if isinstance(result,list) else result.get('items',[])
                put(store,pid,uid,'documents',[{'id':'document:'+d['id'],'document_id':d['id'],'name':d.get('name','文献'),
                    'kind':'literature','status':d.get('status','indexing'),'size_bytes':d.get('size',0),'source':'文献知识库','url':None} for d in docs])
            except Exception:errors.append('文献目录暂未同步')
        sids=set(store.ids(pid,'session'))&store.owned_ids('session',uid)
        if sids:
            try:
                result=await fetch('writing','/api/sessions')
                docs=result if isinstance(result,list) else result.get('sessions',result.get('items',[]))
                put(store,pid,uid,'drafts',[{'id':'draft:'+d['session_id'],'session_id':d['session_id'],'name':d.get('title') or '研究稿件',
                    'kind':'drafts','status':'draft','size_bytes':0,'source':'写作任务','url':None} for d in docs if d['session_id'] in sids])
            except Exception:errors.append('稿件目录暂未同步')
        with store.connect() as db:db.execute('UPDATE catalogue_sync SET updated_at=?,error=? WHERE project_id=? AND user_id=?',(time.time(),'；'.join(sorted(set(errors))),pid,uid))
    finally:JOBS.pop(key,None)


async def catalogue(service,pid,user):
    store=service.get_store();uid=user['id'];key=(str(store.path),pid,uid)
    with store.connect() as db:
        state=db.execute('SELECT * FROM catalogue_sync WHERE project_id=? AND user_id=?',(pid,uid)).fetchone()
    if state is None:
        # Seed from small local metadata snapshots, without contacting other services.
        await asyncio.to_thread(put,store,pid,uid,'tasks',task_rows(store,pid,uid))
        metadata={m.get('dataset_id'):m for m in store.resource_metadata('dataset',uid)}
        put(store,pid,uid,'datasets',[{'id':'dataset:'+did,'dataset_id':did,'kind':'datasets','name':metadata.get(did,{}).get('name') or '已关联数据集',
            'status':'not_checked','sample_count':None,'source':'项目数据集'} for did in store.ids(pid,'dataset') if store.owns('dataset',did,uid)])
        with store.connect() as db:db.execute('INSERT OR IGNORE INTO catalogue_sync VALUES(?,?,0,?)',(pid,uid,''))
    if (state is None or time.time()-state['updated_at']>60) and key not in JOBS:
        JOBS[key]=asyncio.create_task(refresh(service,pid,dict(user),key))
    with store.connect() as db:
        uploaded=[dict(r) for r in db.execute('SELECT * FROM assets WHERE project_id=? AND user_id=? ORDER BY created_at DESC',(pid,uid))]
        cached=[json.loads(r[0]) for r in db.execute('SELECT payload FROM catalogue_entries WHERE project_id=? AND user_id=?',(pid,uid))]
    rows=[];document_ids=set()
    for item in uploaded:
        metadata=json.loads(item['metadata']);document_ids.add(metadata.get('document_id'))
        value={k:item[k] for k in ('id','name','kind','size_bytes','status','created_at')}
        value.update(metadata=metadata,source='项目上传',relative_path=assets.relative_reference(item['path'],'/runtime/incoming/uploads/' if metadata.get('storage')=='analysis' else '/runtime/assets/'),
                     storage_root='analysis_uploads' if metadata.get('storage')=='analysis' else 'project_assets',url=f'/api/projects/{pid}/assets/{item["id"]}/download')
        rows.append(value)
    tids=set(store.ids(pid,'task'))&store.owned_ids('task',uid)
    dids=set(store.ids(pid,'dataset'))&store.owned_ids('dataset',uid)
    sids=set(store.ids(pid,'session'))&store.owned_ids('session',uid)
    for row in cached:
        if row.get('task_id') and row['task_id'] not in tids:continue
        if row.get('dataset_id') and row['dataset_id'] not in dids:continue
        if row.get('session_id') and row['session_id'] not in sids:continue
        if row.get('document_id') and row['document_id'] in document_ids:continue
        rows.append(row)
    listed={r.get('dataset_id') for r in rows if r['kind']=='datasets'}
    metadata={m.get('dataset_id'):m for m in store.resource_metadata('dataset',uid)}
    for did in dids-listed:
        rows.append({'id':'dataset:'+did,'dataset_id':did,'kind':'datasets','name':metadata.get(did,{}).get('name') or '已关联数据集','sample_count':None,'status':'not_checked','source':'项目数据集'})
    return rows
