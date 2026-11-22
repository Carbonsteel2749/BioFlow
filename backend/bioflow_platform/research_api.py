"""Unified table data, tool contracts, versioned plans and durable runs."""
import asyncio
import json
import os
import subprocess
import sys
import uuid
from functools import lru_cache
from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from . import assets
from . import research_store as rs
from .research_contracts import DataOptions,RegisterData,PlanRequest,EditPlan,ConfirmPlan,CONTRACT_VERSION

class ResearchRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handler(request):
            try:return await original(request)
            except KeyError:raise HTTPException(404,'研究记录不存在') from None
            except ValueError as exc:raise HTTPException(422,str(exc)) from None
            except FileNotFoundError:raise HTTPException(503,'统计计算环境未安装') from None
        return handler

@lru_cache(maxsize=4)
def tools_for(interpreter,root):
    return rs.invoke({'mode':'catalog'},Path(root)/'catalog')

def install(app,router,get_store,check_project,files):
    router=APIRouter(prefix=router.prefix,route_class=ResearchRoute)
    def owned(pid,request,identifier,kind=None):
        check_project(pid,request)
        try:return rs.get(get_store(),pid,request.state.user['id'],identifier,kind)
        except KeyError:raise HTTPException(404,'研究记录不存在') from None

    def datasets(pid,uid,ids):
        if len(set(ids))!=len(ids):raise ValueError('输入数据版本不能重复')
        return [rs.get(get_store(),pid,uid,d,'dataset') for d in ids]

    def engine_spec(pid,data,**body):
        return {'project_id':pid,'datasets':[{**d,'directory':str(rs.verify_dataset(d))} for d in data],**body}

    def plan_payload(pid,data,objective,steps,study):
        return {'objective':objective,'steps':steps,'study':study,'dataset_ids':[d['id'] for d in data],
                'snapshot':[{'id':d['id'],'revision':d['revision'],'hashes':d['hashes']} for d in data]}

    async def source(pid,uid,aid):
        with get_store().connect() as db:row=db.execute('SELECT * FROM assets WHERE id=? AND project_id=? AND user_id=?',(aid,pid,uid)).fetchone()
        if row:
            row=dict(row);path=assets.uploaded_path(row,pid,uid);name=row['name'];sha=row['sha256']
        else:
            from .provenance import project_task_ids
            tids=project_task_ids(get_store(),pid,uid)
            row=next((a for tid in tids for a in assets.read_snapshot(tid).get('artifacts',[]) if a['artifact_id']==aid and a['status']=='active'),None)
            if not row or row['task_id'] not in tids:raise HTTPException(404,'文件不属于当前项目')
            path=assets.local_output(row['path']);sha=row['sha256'];name=row.get('file_name') or (path.name if path else '')
        if path is None or assets.availability(path,sha)[0]!='ready':raise HTTPException(409,'来源文件缺失或内容已变化')
        if Path(name).suffix.lower() not in {'.csv','.tsv','.txt','.xlsx','.json'}:raise HTTPException(422,'请选择表格文件')
        if path.stat().st_size>100*1024**2:raise HTTPException(413,'表格导入上限为100 MB')
        return path,name,sha

    async def register(pid,request,aid,options):
        uid=request.state.user['id'];path,name,sha=await source(pid,uid,aid)
        existing=rs.listing(get_store(),pid,uid,'dataset')
        key=rs.fingerprint({'source_sha256':sha,'options':options.model_dump()})
        match=next((d for d in existing if d['import_fingerprint']==key),None)
        if match:rs.verify_dataset(match);return rs.public(match)
        did=uuid.uuid4().hex;directory=rs.root()/uid/pid/'data'/did
        result=await asyncio.to_thread(rs.invoke,{'mode':'inspect','source':str(path),'filename':name,'options':options.model_dump(),'destination':str(directory)},directory/'probe')
        revision=1+max((d['revision'] for d in existing if d.get('source_asset_id')==aid),default=0)
        payload={**result,'filename':name,'source_asset_id':aid,'source_sha256':sha,'role':options.role,'options':options.model_dump(),
                 'revision':revision,'import_fingerprint':key}
        return rs.public(rs.create(get_store(),pid,uid,'dataset','ready',payload,did))

    @router.get('/research/tools')
    async def tools(pid:str,request:Request):
        check_project(pid,request)
        return await asyncio.to_thread(tools_for,rs.python(),str(rs.root()))

    @router.get('/research/data')
    def data_list(pid:str,request:Request):
        check_project(pid,request)
        return {'items':[rs.public(d) for d in rs.listing(get_store(),pid,request.state.user['id'],'dataset')]}

    @router.get('/research/sources')
    async def sources(pid:str,request:Request):
        check_project(pid,request)
        rows=await files.catalogue(pid,request.state.user)
        return {'items':[{'id':r['id'],'name':r['name'],'kind':r['kind']} for r in rows if Path(r['name']).suffix.lower() in {'.csv','.tsv','.txt','.xlsx','.json'} and r.get('status') not in {'missing','empty','integrity_error'}]}

    @router.post('/research/data/register',status_code=201)
    async def register_existing(pid:str,body:RegisterData,request:Request):
        check_project(pid,request)
        return await register(pid,request,body.asset_id,body.options)

    @router.post('/research/data/import',status_code=201)
    async def import_data(pid:str,request:Request,file:UploadFile=File(...),options:str=Form(...)):
        check_project(pid,request);settings=DataOptions.model_validate_json(options)
        if Path(file.filename or '').suffix.lower() not in {'.csv','.tsv','.txt','.xlsx','.json'}:raise HTTPException(422,'请选择表格文件')
        # Existing upload storage is also the single raw-file registry.
        if file.size is not None and file.size>100*1024**2:raise HTTPException(413,'表格导入上限为100 MB')
        asset=await files.upload(pid,request.state.user,file,purpose='results' if settings.role=='results' else 'data')
        return await register(pid,request,asset['id'],settings)

    async def validate(pid,uid,body,data):
        result=await asyncio.to_thread(rs.invoke,engine_spec(pid,data,mode='validate',study=body.study,
            plan={'objective':body.objective,'summary':body.objective,'steps':body.steps,'questions':[]}),rs.root()/uid/pid/'validation'/uuid.uuid4().hex)
        return result

    @router.post('/research/plans',status_code=201)
    async def make_plan(pid:str,body:PlanRequest,request:Request):
        check_project(pid,request);uid=request.state.user['id'];data=datasets(pid,uid,body.dataset_ids)
        if body.mode=='ai':
            result=await asyncio.to_thread(rs.invoke,engine_spec(pid,data,mode='plan',objective=body.objective,study=body.study),rs.root()/uid/pid/'planning'/uuid.uuid4().hex,360)
            proposal=result['plan'];steps=proposal.get('steps') or [];questions=proposal.get('questions') or []
            ready=result['ready'];summary=proposal.get('summary','')
        else:
            if not body.steps:raise HTTPException(422,'请添加分析步骤')
            result=await validate(pid,uid,body,data);steps=result['plan']['steps'];questions=result.get('questions',[]);ready=result['ready'];summary=body.objective
        payload=plan_payload(pid,data,body.objective,steps,body.study);payload.update(summary=summary,questions=questions,origin=body.mode,revision=1)
        payload['fingerprint']=rs.fingerprint(payload)
        return rs.public(rs.create(get_store(),pid,uid,'plan','ready' if ready else 'needs_input',payload))

    @router.put('/research/plans/{identifier}')
    async def edit_plan(pid:str,identifier:str,body:EditPlan,request:Request):
        old=owned(pid,request,identifier,'plan');data=datasets(pid,request.state.user['id'],old['dataset_ids'])
        result=await validate(pid,request.state.user['id'],body,data)
        payload=plan_payload(pid,data,body.objective,result['plan']['steps'],body.study)
        payload.update(summary=body.objective,questions=result.get('questions',[]),origin='edited',revision=old['revision']+1,parent_id=identifier)
        payload['fingerprint']=rs.fingerprint(payload)
        return rs.public(rs.create(get_store(),pid,request.state.user['id'],'plan','ready' if result['ready'] else 'needs_input',payload))

    def start_worker():
        if os.environ.get('BIOFLOW_RESEARCH_AUTOSTART','0')!='1':return
        root=rs.root();root.mkdir(parents=True,exist_ok=True)
        with (root/'queue.log').open('ab') as log:
            env=os.environ.copy();env['BIOFLOW_RESEARCH_ROOT']=str(assets.ROOT)
            subprocess.Popen([sys.executable,'-m','backend.bioflow_platform.research_queue','--database',str(get_store().path)],
              cwd=Path(__file__).resolve().parents[2],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)

    @router.post('/research/plans/{identifier}/confirm',status_code=202)
    async def confirm(pid:str,identifier:str,body:ConfirmPlan,request:Request):
        plan=owned(pid,request,identifier,'plan')
        if plan['status']!='ready' or body.fingerprint!=plan['fingerprint']:raise HTTPException(409,'方案未就绪或已变化，请核对最新方案')
        data=datasets(pid,request.state.user['id'],plan['dataset_ids'])
        for d in data:rs.verify_dataset(d)
        # Re-check method constraints at confirmation, before any computation.
        checked=await validate(pid,request.state.user['id'],EditPlan(objective=plan['objective'],steps=plan['steps'],study=plan['study']),data)
        if not checked['ready']:raise HTTPException(422,checked.get('questions'))
        with get_store().connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute("SELECT * FROM research_records WHERE kind='run' AND project_id=? AND user_id=? AND json_extract(payload,'$.plan_id')=? ORDER BY created_at LIMIT 1",(pid,request.state.user['id'],identifier)).fetchone()
            if prior:run=rs.record(prior)
            else:
                rid=uuid.uuid4().hex;payload={'plan_id':identifier,'plan':{k:plan[k] for k in ['objective','steps','study','dataset_ids','snapshot','fingerprint']},'step_results':[], 'progress':{'completed':0,'total':len(plan['steps'])},'attempt':1}
                db.execute('INSERT INTO research_records(id,kind,project_id,user_id,status,payload) VALUES(?,?,?,?,?,?)',(rid,'run',pid,request.state.user['id'],'queued',rs.dump(payload)))
                run=None
        if run is None:run=rs.get(get_store(),pid,request.state.user['id'],rid,'run');rs.event(get_store(),run,'queued')
        start_worker();return rs.public(run)

    @router.get('/research/runs')
    def runs(pid:str,request:Request):
        check_project(pid,request)
        return {'items':[rs.public(r) for r in rs.listing(get_store(),pid,request.state.user['id'],'run')]}

    @router.get('/research/runs/{identifier}')
    def run(pid:str,identifier:str,request:Request):return rs.public(owned(pid,request,identifier,'run'))

    @router.get('/research/runs/{identifier}/events')
    def events(pid:str,identifier:str,request:Request):
        row=owned(pid,request,identifier,'run')
        with get_store().connect() as db:rows=db.execute('SELECT id,event,payload,created_at FROM research_events WHERE run_id=? AND project_id=? AND user_id=? ORDER BY id',(identifier,pid,row['user_id'])).fetchall()
        return {'items':[{**dict(r),'payload':json.loads(r['payload'])} for r in rows]}

    @router.post('/research/runs/{identifier}/cancel')
    def cancel(pid:str,identifier:str,request:Request):
        row=owned(pid,request,identifier,'run')
        with get_store().connect() as db:
            db.execute("UPDATE research_records SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancelling' END,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('queued','running')",(identifier,))
        rs.event(get_store(),row,'cancel_requested');return rs.public(rs.get(get_store(),pid,row['user_id'],identifier))

    @router.post('/research/runs/{identifier}/resume')
    def resume(pid:str,identifier:str,request:Request):
        row=owned(pid,request,identifier,'run')
        if row['status'] not in {'failed','interrupted','cancelled','completed_with_issues'}:raise HTTPException(409,'当前任务不能恢复')
        for d in datasets(pid,row['user_id'],row['plan']['dataset_ids']):rs.verify_dataset(d)
        with get_store().connect() as db:
            cursor=db.execute("UPDATE research_records SET status='queued',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=?",(identifier,row['status']))
        if not cursor.rowcount:raise HTTPException(409,'任务状态已变化')
        rs.event(get_store(),row,'resume_requested');start_worker();return rs.public(rs.get(get_store(),pid,row['user_id'],identifier))

    @router.get('/research/results/{identifier}')
    def result(pid:str,identifier:str,request:Request):return rs.public(owned(pid,request,identifier,'result'))

    @router.get('/research/artifacts/{identifier}')
    def artifact(pid:str,identifier:str,request:Request):
        row=owned(pid,request,identifier,'artifact');path=assets.ROOT/'runtime/assets'/row['user_id']/pid/row['stored_name']
        if not path.is_file() or rs.digest(path)!=row['sha256']:raise HTTPException(409,'产物文件缺失或校验失败')
        return FileResponse(path,filename=row['filename'])

    from contextlib import asynccontextmanager
    original_lifespan=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application) as current:
            start_worker()
            yield current
    app.router.lifespan_context=lifespan
    app.include_router(router)
