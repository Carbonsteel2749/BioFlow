"""Unified table data, tool contracts, versioned plans and durable runs."""
import asyncio
import io
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
from .research_experience import short_title
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
    def data_list(pid:str,request:Request,task_id:str=''):
        check_project(pid,request)
        ids=None
        if task_id:
            from .research_tasks import owned as task_owned
            ids=set(task_owned(get_store(),pid,request.state.user['id'],task_id).get('dataset_ids',[]))
        return {'items':[rs.public(d) for d in rs.listing(get_store(),pid,request.state.user['id'],'dataset') if ids is None or d['id'] in ids]}

    @router.get('/research/sources')
    async def sources(pid:str,request:Request):
        check_project(pid,request)
        rows=await files.catalogue(pid,request.state.user)
        return {'items':[{'id':r['id'],'name':r['name'],'kind':r['kind']} for r in rows if Path(r['name']).suffix.lower() in {'.csv','.tsv','.txt','.xlsx','.json'} and r.get('status') not in {'missing','empty','integrity_error'}]}

    @router.post('/research/data/register',status_code=201)
    async def register_existing(pid:str,body:RegisterData,request:Request):
        check_project(pid,request)
        if body.task_id:
            from .research_tasks import owned as task_owned
            task_owned(get_store(),pid,request.state.user['id'],body.task_id)
        saved=await register(pid,request,body.asset_id,body.options)
        if body.task_id:
            from .research_tasks import link_dataset
            link_dataset(get_store(),pid,request.state.user['id'],body.task_id,saved)
        return saved

    @router.post('/research/data/import',status_code=201)
    async def import_data(pid:str,request:Request,file:UploadFile=File(...),options:str=Form('{}'),task_id:str=Form('')):
        check_project(pid,request);raw_options=json.loads(options)
        if task_id:
            from .research_tasks import owned as task_owned
            task_owned(get_store(),pid,request.state.user['id'],task_id)
        if Path(file.filename or '').suffix.lower() not in {'.csv','.tsv','.txt','.xlsx','.json'}:raise HTTPException(422,'请选择表格文件')
        # Existing upload storage is also the single raw-file registry.
        if file.size is not None and file.size>100*1024**2:raise HTTPException(413,'表格导入上限为100 MB')
        asset=await files.upload(pid,request.state.user,file,purpose='results' if raw_options.get('role')=='results' else 'data',details={'task_id':task_id} if task_id else {})
        if not raw_options.get('role') or raw_options.get('role')=='auto':
            path,name,sha=await source(pid,request.state.user['id'],asset['id'])
            references=[s for d in rs.listing(get_store(),pid,request.state.user['id'],'dataset') for s in d.get('samples',[])]
            raw_options.pop('role',None)
            detected=await asyncio.to_thread(rs.invoke,{'mode':'recognize','source':str(path),'filename':name,'options':raw_options,'reference_samples':references},rs.root()/'recognition'/uuid.uuid4().hex)
            raw_options=detected['options']
        saved=await register(pid,request,asset['id'],DataOptions.model_validate(raw_options))
        if task_id:
            from .research_tasks import link_dataset
            link_dataset(get_store(),pid,request.state.user['id'],task_id,saved)
        return saved

    async def validate(pid,uid,body,data):
        result=await asyncio.to_thread(rs.invoke,engine_spec(pid,data,mode='validate',study=body.study,
            plan={'objective':body.objective,'summary':body.objective,'steps':body.steps,'questions':[]}),rs.root()/uid/pid/'validation'/uuid.uuid4().hex)
        return result

    @router.post('/research/plans',status_code=201)
    async def make_plan(pid:str,body:PlanRequest,request:Request):
        check_project(pid,request);uid=request.state.user['id'];data=datasets(pid,uid,body.dataset_ids)
        task_row=None
        if body.task_id:
            from .research_tasks import owned as task_owned
            task_row=task_owned(get_store(),pid,uid,body.task_id)
            if set(body.dataset_ids)-set(task_row.get('dataset_ids',[])):raise HTTPException(422,'方案输入必须先加入本任务')
        if body.mode=='ai':
            result=await asyncio.to_thread(rs.invoke,engine_spec(pid,data,mode='plan',objective=body.objective,study=body.study,known_context=(task_row or {}).get('resolved_context',{}),latest_instruction=next((m['text'] for m in reversed((task_row or {}).get('messages',[])) if m.get('role')=='user' and m.get('type')=='text'),body.objective)),rs.root()/uid/pid/'planning'/uuid.uuid4().hex,360)
            proposal=result['plan'];steps=proposal.get('steps') or [];questions=proposal.get('questions') or []
            ready=result['ready'];summary=proposal.get('summary','')
        else:
            if not body.steps:raise HTTPException(422,'请添加分析步骤')
            result=await validate(pid,uid,body,data);steps=result['plan']['steps'];questions=result.get('questions',[]);ready=result['ready'];summary=body.objective
        payload=plan_payload(pid,data,body.objective,steps,result.get('study',body.study));payload.update(summary=summary,questions=questions,origin=body.mode,revision=1)
        if result.get('planning_diagnostics',{}).get('source') in {'data_profile','rule_router'}:
            payload['origin']=result['planning_diagnostics']['source']
        for key in ('data_profile','planning_diagnostics','failure','resolved_context'):
            if key in result:payload[key]=result[key]
        if body.mode=='ai' and proposal.get('clarifications'):payload['clarifications']=proposal['clarifications']
        if body.mode=='ai' and proposal.get('research_design'):payload['research_design']=proposal['research_design']
        from .research_experience import short_title
        payload['title']=proposal.get('title') if body.mode=='ai' else short_title(payload)
        if task_row:
            payload['task_id']=task_row['id'];payload['revision']=len(task_row.get('plan_ids',[]))+1
            if task_row.get('plan_ids'):payload['parent_id']=task_row['plan_ids'][-1]
        payload['fingerprint']=rs.fingerprint(payload)
        return rs.public(rs.create(get_store(),pid,uid,'plan','planning_failed' if result.get('failure') else 'ready' if ready else 'needs_input',payload))

    @router.put('/research/plans/{identifier}')
    async def edit_plan(pid:str,identifier:str,body:EditPlan,request:Request):
        old=owned(pid,request,identifier,'plan')
        input_ids=old['dataset_ids']
        if old.get('task_id'):
            from .research_tasks import owned as task_owned
            input_ids=task_owned(get_store(),pid,request.state.user['id'],old['task_id']).get('dataset_ids',[])
        data=datasets(pid,request.state.user['id'],input_ids)
        result=await validate(pid,request.state.user['id'],body,data)
        payload=plan_payload(pid,data,body.objective,result['plan']['steps'],body.study)
        if old.get('task_id'):payload['task_id']=old['task_id']
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
                rid=uuid.uuid4().hex;payload={'task_id':plan.get('task_id'),'title':plan.get('title') or short_title(plan),'plan_id':identifier,'plan':{k:plan[k] for k in ['objective','steps','study','dataset_ids','snapshot','fingerprint']},'step_results':[], 'progress':{'completed':0,'total':len(plan['steps'])},'attempt':1}
                db.execute('INSERT INTO research_records(id,kind,project_id,user_id,status,payload) VALUES(?,?,?,?,?,?)',(rid,'run',pid,request.state.user['id'],'queued',rs.dump(payload)))
                run=None
        if run is None:run=rs.get(get_store(),pid,request.state.user['id'],rid,'run');rs.event(get_store(),run,'queued')
        start_worker();return rs.public(run)

    @router.get('/research/runs')
    def runs(pid:str,request:Request):
        check_project(pid,request)
        from .research_experience import short_title
        return {'items':[{**rs.public(r),'title':r.get('title') or short_title(r['plan'])} for r in rs.listing(get_store(),pid,request.state.user['id'],'run')]}

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
        if row.get('needs_plan_confirmation'):
            raise HTTPException(409,'本次修复需要变更分析方案，请先确认新版方案后再执行。')
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


    @router.get('/research/data/{identifier}/table')
    async def preview_table(pid:str,identifier:str,request:Request,offset:int=0,limit:int=50):
        row=owned(pid,request,identifier,'dataset')
        return await asyncio.to_thread(rs.invoke,{'mode':'table_preview','directory':str(rs.verify_dataset(row)),'index_name':row['schema']['index'],'offset':max(0,offset),'limit':max(1,min(limit,100))},rs.root()/'previews'/uuid.uuid4().hex)

    @router.post('/research/data/{identifier}/correction')
    async def correction(pid:str,identifier:str,request:Request,body:dict):
        row=owned(pid,request,identifier,'dataset');text=str(body.get('instruction','')).strip()
        if not text or len(text)>4000:raise HTTPException(422,'请输入4000字以内的修正要求')
        return await asyncio.to_thread(rs.invoke,{'mode':'correct_settings','directory':str(rs.verify_dataset(row)),'options':row['options'],'engine_info':row['engine_info'],'instruction':text},rs.root()/'corrections'/uuid.uuid4().hex)

    @router.post('/research/data/{identifier}/edit',status_code=201)
    async def edit_data(pid:str,identifier:str,request:Request,body:dict):
        row=owned(pid,request,identifier,'dataset');edits=body.get('edits',[])
        task_id=body.get('task_id')
        if task_id:
            from .research_tasks import owned as task_owned
            if identifier not in task_owned(get_store(),pid,row['user_id'],task_id).get('dataset_ids',[]):raise HTTPException(404,'数据不属于本任务')
        if not isinstance(edits,list) or not edits or len(edits)>2000:raise HTTPException(422,'每次提交1至2000个单元格修改')
        for e in edits:
            if not isinstance(e,dict) or not isinstance(e.get('row'),int) or not isinstance(e.get('column'),int) or not isinstance(e.get('value'),(str,int,float)):raise HTTPException(422,'单元格修改格式错误')
        target=rs.root()/row['user_id']/pid/'edits'/(uuid.uuid4().hex+'.csv')
        checked=await asyncio.to_thread(rs.invoke,{'mode':'edit_data','directory':str(rs.verify_dataset(row)),'options':row['options'],'engine_info':row['engine_info'],'edits':edits,'destination':str(target)},target.parent/'checks'/uuid.uuid4().hex)
        try:
            asset=await files.upload(pid,request.state.user,UploadFile(filename=Path(row['filename']).stem+'-edited.csv',file=io.BytesIO(target.read_bytes())),purpose='results' if row['role']=='results' else 'data',details={'parent_refs':[row['source_asset_id']]})
            saved=await register(pid,request,asset['id'],DataOptions.model_validate(checked['options']))
            if saved['id']!=identifier:
                current=rs.get(get_store(),pid,row['user_id'],saved['id'],'dataset')
                saved=rs.public(rs.update(get_store(),current,'ready',parent_id=identifier,revision=row['revision']+1,edit_count=len(edits)))
            if task_id:
                from .research_tasks import link_dataset
                link_dataset(get_store(),pid,row['user_id'],task_id,saved,replace_id=identifier)
            return saved
        finally:target.unlink(missing_ok=True)

    @router.get('/research/runs/{identifier}/report')
    async def report(pid:str,identifier:str,request:Request):
        from .research_experience import build_report
        row=owned(pid,request,identifier,'run')
        return row.get('report') or build_report(row,use_ai=False)

    @router.post('/research/runs/{identifier}/report')
    async def interpret_report(pid:str,identifier:str,request:Request):
        row=owned(pid,request,identifier,'run')
        if row['status'] in {'queued','running','cancelling'}:raise HTTPException(409,'分析仍在运行，完成后生成报告')
        if row.get('report',{}).get('interpretation_source')=='ai_evidence_summary':return row['report']
        generated=await asyncio.to_thread(rs.invoke,{'mode':'report','run':rs.public(row)},rs.root()/row['user_id']/pid/'reports'/identifier,120)
        current=owned(pid,request,identifier,'run')
        from .research_queue import save_active
        save_active(get_store(),current,report=generated)
        return generated

    from .research_tasks import install as install_tasks
    install_tasks(app,router,get_store,check_project,files,source,register,make_plan,confirm,edit_plan,report,interpret_report)

    from contextlib import asynccontextmanager
    original_lifespan=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with original_lifespan(application) as current:
            start_worker()
            yield current
    app.router.lifespan_context=lifespan
    app.include_router(router)
