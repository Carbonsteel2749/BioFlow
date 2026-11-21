"""Owned publication bindings to the existing analysis template/version service.

The engine owns executable graphs. This layer stores immutable visual releases
and task provenance, never a second independently executable template format.
"""
import copy
import asyncio
import json
import re
from fastapi import HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field

VIEW_TYPES={'start','analysis_group','end','pathways','figures','interpret_llm','repair_llm'}
SAFE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')

def execution_graph(design,registry):
    nodes=design.get('nodes',[]);edges=design.get('edges',[])
    if not isinstance(nodes,list) or not 1<=len(nodes)<=100 or not isinstance(edges,list) or len(edges)>300:
        raise ValueError('流程大小不符合限制')
    catalog={n['type']:n for n in registry['nodes']};ids=set();active=set();result=[]
    for n in nodes:
        if not isinstance(n,dict):raise ValueError('节点必须为对象')
        nid=n.get('id');kind=n.get('type');params=n.get('parameters',{})
        if not isinstance(nid,str) or not SAFE.fullmatch(nid) or nid in ids:raise ValueError('节点编号非法或重复')
        ids.add(nid)
        if n.get('custom'):raise ValueError('自定义参数尚未登记执行映射，请先登记再发布')
        if kind in VIEW_TYPES:
            if kind=='repair_llm' and params.get('enabled'):raise ValueError('请在创建任务时启用受限修复；画布模型切换尚不可执行')
            continue
        if kind not in catalog:raise ValueError('分析方法尚未接入执行后端：'+str(kind))
        if not isinstance(params,dict):raise ValueError('节点参数格式错误')
        allowed=catalog[kind].get('parameters_schema',{})
        clean={k:v for k,v in params.items() if not (kind=='fastq_input' and k in {'dataset_id','metadata_note'})}
        if set(clean)-set(allowed):raise ValueError('节点含未登记参数：'+nid)
        active.add(nid);result.append({'id':nid,'type':kind,'parameters':clean})
    links=[];risks=set();by_id={n['id']:n for n in nodes}
    for e in edges:
        if not isinstance(e,dict) or not all(isinstance(e.get(k),str) for k in ('id','source','sourcePort','target','targetPort')):raise ValueError('连接字段不完整')
        if e.get('source') not in ids or e.get('target') not in ids:raise ValueError('连接引用不存在的节点')
        if e['source'] in active and e['target'] in active:
            source=by_id[e['source']];target=by_id[e['target']]
            output=next((p for p in catalog[source['type']].get('outputs',[]) if p['id']==e['sourcePort']),{})
            if e.get('acknowledged'):
                if target['type'] in {'taxonomy','functional_annotation'} and output.get('data_type')!='paired_host_removed_reads':risks.add('risk:'+target['id']+':annotation_without_host_depletion')
                if target['type']=='host_depletion' and output.get('data_type')!='paired_clean_reads':risks.add('risk:'+target['id']+':host_depletion_without_fastp')
            links.append({'id':e['id'],'source_node':e['source'],'source_port':e['sourcePort'],
                          'target_node':e['target'],'target_port':e['targetPort'],
                          'acknowledged_risks':['raw_annotation'] if e.get('acknowledged') else []})
    # Risk acknowledgement is an engine graph-level contract; retain only
    # documented edge fields and let the engine revalidate every graph.
    for e in links:e.pop('acknowledged_risks',None)
    return {'schema_version':'1.0','nodes':result,'edges':links,'accepted_risks':sorted(risks)}

def initialize(store):
    with store.connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS workflow_visual_releases(
          user_id TEXT NOT NULL,template_id TEXT NOT NULL,version INTEGER NOT NULL,
          payload TEXT NOT NULL,PRIMARY KEY(user_id,template_id,version));
          CREATE TABLE IF NOT EXISTS workflow_task_snapshots(
          user_id TEXT NOT NULL,project_id TEXT NOT NULL,task_id TEXT PRIMARY KEY,payload TEXT NOT NULL);''')

def save_release(store,uid,record,design):
    visual=copy.deepcopy(design)
    for node in visual.get('nodes',[]):
        if node['type']=='fastq_input':node.setdefault('parameters',{})['dataset_id']=''
    payload={'record':record,'design':visual,'template_id':record['template_id'],'version':record['version']}
    with store.connect() as db:
        db.execute('INSERT INTO workflow_visual_releases VALUES(?,?,?,?)',(uid,record['template_id'],record['version'],json.dumps(payload,ensure_ascii=False,allow_nan=False)))

def release(store,uid,tid,version):
    with store.connect() as db:
        row=db.execute('SELECT payload FROM workflow_visual_releases WHERE user_id=? AND template_id=? AND version=?',(uid,tid,version)).fetchone()
    if not row:raise KeyError(tid)
    return json.loads(row[0])

def save_snapshot(store,uid,pid,tid,record,workflow):
    payload={**copy.deepcopy(record),'workflow':copy.deepcopy(workflow)}
    with store.connect() as db:db.execute('INSERT INTO workflow_task_snapshots VALUES(?,?,?,?)',(uid,pid,tid,json.dumps(payload,ensure_ascii=False,allow_nan=False)))

def snapshot(store,uid,pid,tid):
    with store.connect() as db:row=db.execute('SELECT payload FROM workflow_task_snapshots WHERE user_id=? AND project_id=? AND task_id=?',(uid,pid,tid)).fetchone()
    if not row:raise KeyError(tid)
    return json.loads(row[0])

class Publication(BaseModel):
    model_config=ConfigDict(extra='forbid')
    design:dict
    template_id:str|None=Field(None,pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
    base_version:int|None=Field(None,ge=1)

class Submission(BaseModel):
    model_config=ConfigDict(extra='forbid')
    template_id:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
    version:int=Field(ge=1)
    name:str=Field(min_length=1,max_length=120)
    dataset_id:str=Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
    node_parameters:dict=Field(default_factory=dict)
    auto_repair:bool=False

def install(router,get_store,check_project,call):
    initialize(get_store())
    def get_release(uid,tid,version):
        try:return release(get_store(),uid,tid,version)
        except KeyError:raise HTTPException(404,'流程版本不存在或不属于当前账户')

    async def selected(uid,tid,version):
        if tid.startswith('builtin-'):
            record=await call('analysis','/api/workflows/templates/'+tid+'?version='+str(version))
            return {'template_id':tid,'version':version,'record':record,'design':None}
        return get_release(uid,tid,version)

    @router.get('/analysis/workflows')
    async def listing(pid:str,request:Request):
        check_project(pid,request);uid=request.state.user['id']
        upstream=await call('analysis','/api/workflows/templates')
        rows=upstream if isinstance(upstream,list) else upstream.get('items',[])
        result=[{'template_id':r['template_id'],'version':r['version'],'record':r,'design':None} for r in rows if r.get('builtin') or r['template_id'].startswith('builtin-')]
        with get_store().connect() as db:
            records=db.execute('SELECT payload FROM workflow_visual_releases WHERE user_id=? ORDER BY version',(uid,)).fetchall()
        latest={}
        for row in records:
            r=json.loads(row[0]);latest[r['template_id']]=r
        return {'items':result+list(latest.values())}

    @router.post('/analysis/workflows/publish',status_code=201)
    async def publish(pid:str,body:Publication,request:Request):
        check_project(pid,request);uid=request.state.user['id'];store=get_store()
        if len(json.dumps(body.design,ensure_ascii=False))>2_000_000:raise HTTPException(422,'设计超过 2 MB 限制')
        if body.template_id:
            if not body.base_version:raise HTTPException(422,'更新需要原版本号')
            get_release(uid,body.template_id,body.base_version)
        registry=await call('analysis','/api/workflows/registry')
        try:graph=execution_graph(body.design,registry)
        except ValueError as e:raise HTTPException(422,str(e))
        name=str(body.design.get('name','')).strip()
        if not name or len(name)>120:raise HTTPException(422,'流程名称应为 1–120 字')
        payload={'name':name,'workflow':graph,'description':'统一工作台发布的流程；可视化布局独立保留，执行定义由分析服务校验。'}
        method='POST';path='/api/workflows/templates'
        if body.template_id:
            method='PUT';path+='/'+body.template_id;payload['base_version']=body.base_version
        record=await call('analysis',path,method,payload)
        store.claim('workflow_template',record['template_id'],uid)
        save_release(store,uid,record,body.design)
        return release(store,uid,record['template_id'],record['version'])

    @router.post('/analysis/workflows/start',status_code=201)
    async def start(pid:str,body:Submission,request:Request):
        check_project(pid,request);uid=request.state.user['id'];store=get_store()
        if not body.name.strip():raise HTTPException(422,'任务名称不能为空')
        if not store.owns('dataset',body.dataset_id,uid) or body.dataset_id not in store.ids(pid,'dataset'):raise HTTPException(404,'请选择当前项目的数据集')
        record=await selected(uid,body.template_id,body.version)
        workflow=copy.deepcopy(record['record']['workflow']);nodes={n['id']:n for n in workflow['nodes']}
        registry=await call('analysis','/api/workflows/registry')
        schemas={n['type']:n.get('parameters_schema',{}) for n in registry['nodes']}
        if set(body.node_parameters)-set(nodes):raise HTTPException(422,'参数引用未知节点')
        for nid,params in body.node_parameters.items():
            if not isinstance(params,dict):raise HTTPException(422,'节点参数格式不正确')
            nodes[nid]['parameters'].update(params)
        for node in nodes.values():
            if node['type'] not in schemas:raise HTTPException(422,'流程方法已不再登记，请重新发布')
            schema=schemas[node['type']]
            if set(node['parameters'])-set(schema):raise HTTPException(422,'参数未在当前方法登记')
            node['parameters']={**{k:copy.deepcopy(rule['default']) for k,rule in schema.items() if 'default' in rule},**node['parameters']}
        record={**record,'parameter_registry':registry}
        reused=await call('analysis','/api/datasets/'+body.dataset_id+'/reuse','POST',{})
        run=await call('analysis','/api/workflows/runs','POST',{'name':body.name.strip(),'manifest_path':reused['manifest_path'],'workflow':workflow,'auto_repair':body.auto_repair})
        tid=run['task_id'];store.claim('workflow',tid,uid);store.link(pid,'workflow',tid)
        save_snapshot(store,uid,pid,tid,record,workflow)
        return {**run,'template_id':body.template_id,'template_version':body.version}

    @router.get('/analysis/workflows/runs')
    async def runs(pid:str,request:Request,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0)):
        check_project(pid,request);store=get_store();uid=request.state.user['id']
        with store.connect() as db:ids=[r[0] for r in db.execute("SELECT external_id FROM links WHERE project_id=? AND kind='workflow' ORDER BY rowid DESC",(pid,))]
        owned=store.owned_ids('workflow',uid);ids=[tid for tid in ids if tid in owned]
        sem=asyncio.Semaphore(5)
        async def fetch(tid):
            async with sem:return await call('analysis','/api/workflows/runs/'+tid)
        records=await asyncio.gather(*(fetch(tid) for tid in ids[offset:offset+limit]))
        return {'items':records,'total':len(ids),'offset':offset,'limit':limit}

    @router.get('/analysis/workflows/runs/{tid}')
    async def detail(pid:str,tid:str,request:Request):
        check_project(pid,request);store=get_store();uid=request.state.user['id']
        if tid not in store.ids(pid,'workflow') or not store.owns('workflow',tid,uid):raise HTTPException(404,'任务不属于当前项目')
        run=await call('analysis','/api/workflows/runs/'+tid)
        try:snap=snapshot(store,uid,pid,tid)
        except KeyError:
            snap={'workflow':await call('analysis','/api/workflows/runs/'+tid+'/workflow'),'design':None}
        return {'run':run,'snapshot':snap}
