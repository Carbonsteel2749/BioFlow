"""Project-owned task membership and evidence-backed asset relationships."""
import json
from typing import Literal
from fastapi import HTTPException,Request
from pydantic import BaseModel,Field
from . import assets


def task_index(store,pid,uid):
    result={}
    for tid in sorted(set(store.ids(pid,'task')) & store.owned_ids('task',uid)):
        task=assets.read_snapshot(tid).get('task',{})
        result[tid]={'id':tid,'name':task.get('name') or tid,'status':task.get('status','unknown'),
                     'parameters':task.get('parameters',{}),'dataset_id':task.get('dataset_id')}
    return result


def normal_doi(value):
    text=str(value or '').strip().lower()
    for prefix in ('https://doi.org/','http://doi.org/','doi:'):
        if text.startswith(prefix):text=text[len(prefix):]
    return text.strip().rstrip('.')


def graph(store,pid,uid,rows):
    tasks=task_index(store,pid,uid);index={r['id']:r for r in rows};members={k:set() for k in index};edges={}
    def edge(a,b,relation,origin):
        if a in index and b in index and a!=b:edges[a,b,relation]={'asset_id':a,'source_id':b,'relation':relation,'origin':origin}
    for row in rows:
        for tid in [row.get('task_id'),row.get('metadata',{}).get('task_id')]:
            if tid in tasks:members[row['id']].add(tid)
        if row.get('dataset_id'):
            for tid,task in tasks.items():
                if task['dataset_id']==row['dataset_id']:members[row['id']].add(tid)
    raw_uploads={}
    for row in rows:
        if row.get('storage_root')=='analysis_inputs':
            parts=str(row.get('relative_path') or '').replace('\\','/').split('/')
            if len(parts)>1 and parts[0]=='uploads':raw_uploads.setdefault(parts[1],set()).update(members[row['id']])
    for row in rows:
        members[row['id']].update(raw_uploads.get(row.get('metadata',{}).get('analysis_upload_id'),set()))
    inputs={}
    for row in rows:
        if row['kind']=='data' and row.get('task_id') in tasks:inputs.setdefault(row['task_id'],[]).append(row['id'])
    # Exact persisted identifiers only; never join unrelated files by filename.
    artifacts={r['artifact_id']:r['id'] for r in rows if r.get('artifact_id')}
    dois={}
    for row in rows:
        if row['kind']=='literature':
            doi=normal_doi(row.get('metadata',{}).get('doi'))
            if doi:dois.setdefault(doi,[]).append(row['id'])
    for row in rows:
        source=row.get('source_id')
        if source:
            if source.startswith('artifact:'):source=artifacts.get(source.split(':',1)[1])
            edge(row['id'],source,'derived_from','recorded')
        for reference in row.get('references',[]):
            for paper in dois.get(normal_doi(reference.get('doi')),[]):edge(row['id'],paper,'cites','recorded')
        if row['kind'] in {'results','charts'} and row.get('task_id') in tasks:
            for raw_id in inputs.get(row['task_id'],[]):edge(row['id'],raw_id,'derived_from','task_manifest')
    with store.connect() as db:
        manual=db.execute('SELECT * FROM asset_task_links WHERE project_id=? AND user_id=?',(pid,uid)).fetchall()
        relations=db.execute('SELECT * FROM asset_relations WHERE project_id=? AND user_id=?',(pid,uid)).fetchall()
    for r in manual:
        if r['asset_id'] in members and r['task_id'] in tasks:members[r['asset_id']].add(r['task_id'])
    for r in relations:edge(r['asset_id'],r['source_id'],r['relation'],'manual')
    # Outputs inherit their direct source's tasks; inputs/references inherit only
    # their consumer's explicitly recorded tasks (no transitive task contamination).
    initial={k:set(v) for k,v in members.items()}
    for e in edges.values():
        members[e['source_id']].update(initial[e['asset_id']])
    for _ in range(len(index)):
        changed=False
        for e in edges.values():
            if e['relation']=='cites' or initial[e['asset_id']]:continue
            before=len(members[e['asset_id']]);members[e['asset_id']].update(members[e['source_id']]);changed|=before!=len(members[e['asset_id']])
        if not changed:break
    return tasks,index,members,list(edges.values()),manual


def decorate(store,pid,uid,rows):
    tasks,index,members,edges,_=graph(store,pid,uid,rows)
    for row in rows:
        row['task_ids']=sorted(members[row['id']]);row['task_names']=[tasks[t]['name'] for t in row['task_ids']]
    return rows


class TaskLinks(BaseModel):
    task_ids:list[str]=Field(default_factory=list,max_length=100)


class Relation(BaseModel):
    source_id:str=Field(min_length=1,max_length=240)
    relation:Literal['derived_from','uses','cites']='derived_from'


def install_provenance(router,get_store,check_project,files):
    async def inventory(pid,request):
        check_project(pid,request)
        rows=await files.catalogue(pid,request.state.user)
        return graph(get_store(),pid,request.state.user['id'],rows)

    @router.get('/asset-tasks')
    async def groups(pid:str,request:Request):
        tasks,index,members,edges,_=await inventory(pid,request)
        return {'items':[{**{k:v for k,v in t.items() if k!='parameters'},'count':sum(tid in m for m in members.values())} for tid,t in tasks.items()],
                'unclassified':sum(not m for m in members.values())}

    @router.get('/asset-links')
    async def detail(pid:str,asset_id:str,request:Request):
        tasks,index,members,edges,manual=await inventory(pid,request)
        if asset_id not in index:raise HTTPException(404,'资料不属于当前项目')
        def brief(key):
            row=index[key]
            return {k:row.get(k) for k in ('id','name','kind','status','url','figure_id','session_id','document_id','dataset_id')}
        related=[{**e,'asset':brief(e['asset_id']),'source':brief(e['source_id'])} for e in edges if asset_id in (e['asset_id'],e['source_id'])]
        tids=members[asset_id]
        return {'asset':brief(asset_id),'tasks':[tasks[t] for t in sorted(tids)],
                'manual_task_ids':[r['task_id'] for r in manual if r['asset_id']==asset_id],
                'relations':related,'references':index[asset_id].get('references',[]),
                'processing':index[asset_id].get('processing'),
                'unresolved_source':index[asset_id].get('source_record') if index[asset_id].get('source_id') and not any(e['asset_id']==asset_id and e['relation']=='derived_from' for e in edges) else None,
                'context':[brief(key) for key,r in index.items() if key!=asset_id and (members[key]&tids) and r['kind'] in {'data','results','charts','drafts'}][:100],
                'choices':[brief(key) for key in index if key!=asset_id]}

    @router.put('/asset-task-links')
    async def assign(pid:str,asset_id:str,body:TaskLinks,request:Request):
        tasks,index,members,edges,_=await inventory(pid,request)
        if asset_id not in index:raise HTTPException(404,'资料不属于当前项目')
        if set(body.task_ids)-tasks.keys():raise HTTPException(422,'只能关联当前项目中属于你的任务')
        uid=request.state.user['id']
        with get_store().connect() as db:
            db.execute('DELETE FROM asset_task_links WHERE project_id=? AND user_id=? AND asset_id=?',(pid,uid,asset_id))
            db.executemany('INSERT INTO asset_task_links VALUES(?,?,?,?)',[(pid,uid,asset_id,t) for t in set(body.task_ids)])
        get_store().event(pid,'调整资料任务关联',{'asset_id':asset_id,'task_ids':body.task_ids})
        return {'ok':True}

    @router.post('/asset-links')
    async def link(pid:str,asset_id:str,body:Relation,request:Request):
        tasks,index,members,edges,_=await inventory(pid,request)
        if asset_id not in index or body.source_id not in index:raise HTTPException(404,'关联资料不属于当前项目')
        if any(e['asset_id']==asset_id and e['source_id']==body.source_id and e['relation']==body.relation and e['origin']!='manual' for e in edges):raise HTTPException(409,'此关系由运行记录确认，无需重复添加')
        if asset_id==body.source_id:raise HTTPException(422,'不能关联文件自身')
        if body.relation=='cites' and (index[asset_id]['kind']!='drafts' or index[body.source_id]['kind']!='literature'):raise HTTPException(422,'参考文献关系只能从稿件连接到文献')
        if body.relation!='cites':
            pending=[body.source_id];seen=set()
            while pending:
                node=pending.pop()
                if node==asset_id:raise HTTPException(422,'来源关系不能构成循环')
                if node in seen:continue
                seen.add(node);pending.extend(e['source_id'] for e in edges if e['asset_id']==node and e['relation']!='cites')
        with get_store().connect() as db:db.execute('INSERT OR IGNORE INTO asset_relations VALUES(?,?,?,?,?)',(pid,request.state.user['id'],asset_id,body.source_id,body.relation))
        get_store().event(pid,'关联资料来源',{'asset_id':asset_id,**body.model_dump()})
        return {'ok':True}

    @router.delete('/asset-links')
    async def unlink(pid:str,asset_id:str,body:Relation,request:Request):
        tasks,index,members,edges,_=await inventory(pid,request)
        if asset_id not in index or body.source_id not in index:raise HTTPException(404,'资料不属于当前项目')
        with get_store().connect() as db:db.execute('DELETE FROM asset_relations WHERE project_id=? AND user_id=? AND asset_id=? AND source_id=? AND relation=?',(pid,request.state.user['id'],asset_id,body.source_id,body.relation))
        return {'ok':True}
