import asyncio
import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from bioflow_platform import unified_workflows as u
from bioflow_platform.access import authorize
from bioflow_platform.store import Store
from bioflow_platform.input_datasets import ImportGateway
from test_typed_reuse import private_metadata_catalog


@pytest.mark.parametrize('role',['user','developer'])
def test_direct_proxy_typed_runs_require_project_route(tmp_path,role):
    async def body():return {'input_bindings':{'source':'foreign'}}
    request=SimpleNamespace(state=SimpleNamespace(user={'id':'alice','role':role}),method='POST',json=body)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(authorize(Store(tmp_path/'s.db'),'analysis','api/workflows/runs',request,{}))
    assert exc.value.status_code==403


@pytest.mark.parametrize('owner',['alice','bob'])
def test_artifact_proxy_accepts_only_owned_workflow_producer(tmp_path,owner):
    store=Store(tmp_path/'s.db');store.claim('workflow','run1','alice')
    request=SimpleNamespace(state=SimpleNamespace(user={'id':owner,'role':'developer'}),method='GET')
    async def call(*args):return {'task_id':'run1'}
    if owner=='alice':asyncio.run(authorize(store,'analysis','api/artifacts/a',request,{'call':call}))
    else:
        with pytest.raises(HTTPException):asyncio.run(authorize(store,'analysis','api/artifacts/a',request,{'call':call}))


@pytest.fixture
def bound(tmp_path):
    store=Store(tmp_path/'s.db');project=store.create('p','','alice');pid=project['id']
    ImportGateway(lambda:store,tmp_path/'incoming',lambda *args:None)
    record={'dataset_id':'typed','spec':{'kind':'matrix','unit':'fraction'},'contract':{'data_type':'abundance_matrix','unit':'fraction','trust':'external_unverified'},'record_sha256':'frozen'}
    store.claim('input_dataset','typed','alice');store.link(pid,'input_dataset','typed')
    with store.connect() as db:
        db.execute("INSERT INTO input_dataset_imports(id,project_id,user_id,idempotency_key,request_json,status,record_json,provenance_json) VALUES(?,?,'alice','key','{}','ready',?,?)",('typed',pid,json.dumps(record),json.dumps([{'id':'asset:a','sha256':'original'}])))
    workflow={'schema_version':'1.0','nodes':[{'id':'source','type':'abundance_input','parameters':{}},{'id':'summary','type':'matrix_summary','parameters':{}}],'edges':[]}
    registry={'nodes':[{'type':'abundance_input','parameters_schema':{},'inputs':[],'outputs':[{'id':'matrix','data_type':'abundance_matrix'}]},{'type':'matrix_summary','parameters_schema':{}}]}
    calls=[]
    async def upstream(service,path,method='GET',payload=None):
        if path=='/api/tasks/run1/artifacts?metadata_only=true':return [{'artifact_id':'a','task_id':'run1','file_name':'x.png','path':'/secret/x.png','metadata':{'path':'/secret'}}]
        if path=='/api/tasks/bad/artifacts?metadata_only=true':return [{'artifact_id':'a','task_id':'foreign'}]
        if path.endswith('/registry'):return registry
        if path.startswith('/api/workflows/templates/builtin-'):return {'workflow':workflow}
        if path=='/api/workflows/runs':calls.append(copy.deepcopy(payload));return {'task_id':'run1'}
        raise AssertionError(path)
    app=FastAPI();router=APIRouter(prefix='/api/projects/{pid}')
    @app.middleware('http')
    async def auth(request,call_next):
        request.state.user={'id':request.headers.get('x-user','alice'),'role':'developer'}
        return await call_next(request)
    u.install(router,lambda:store,lambda p,r:None,upstream);app.include_router(router)
    return store,TestClient(app),pid,calls,record


def test_project_workflow_artifacts_strip_paths_and_enforce_owner(bound):
    store,c,pid,_,_=bound
    store.claim('workflow','run1','alice');store.link(pid,'workflow','run1')
    url=f'/api/projects/{pid}/analysis/workflows/runs/run1/artifacts'
    response=c.get(url);assert response.status_code==200,response.text
    assert response.json()['items'][0]['file_name']=='x.png'
    assert 'secret' not in response.text and 'metadata' not in response.text
    assert c.get(url,headers={'x-user':'bob'}).status_code==404
    other=store.create('other','','alice')['id']
    assert c.get(url.replace(pid,other)).status_code==404
    store.claim('workflow','bad','alice');store.link(pid,'workflow','bad')
    assert c.get(url.replace('run1','bad')).status_code==502


def test_project_outputs_use_real_private_metadata_only_handler(tmp_path,private_metadata_catalog):
    private,record,invocations=private_metadata_catalog
    store=Store(tmp_path/'project.db');pid=store.create('p','','alice')['id']
    store.claim('workflow','run1','alice');store.link(pid,'workflow','run1')
    async def upstream(service,path,method='GET',payload=None):
        response=private.request(method,path,json=payload)
        response.raise_for_status();return response.json()
    app=FastAPI();router=APIRouter(prefix='/api/projects/{pid}')
    @app.middleware('http')
    async def auth(request,call_next):
        request.state.user={'id':'alice','role':'developer'}
        return await call_next(request)
    u.install(router,lambda:store,lambda p,r:None,upstream);app.include_router(router)
    response=TestClient(app).get(f'/api/projects/{pid}/analysis/workflows/runs/run1/artifacts')
    assert response.status_code==200,response.text
    assert response.json()['items'][0]['artifact_id']==record['artifact_id']
    assert 'source_file' not in response.text and invocations==[]


def body():return {'template_id':'builtin-matrix','version':1,'name':'matrix','input_bindings':{'source':'typed'}}


def test_typed_submission_freezes_owned_record_and_passes_only_ids(bound):
    store,c,pid,calls,record=bound
    response=c.post(f'/api/projects/{pid}/analysis/workflows/start',json=body())
    assert response.status_code==201,response.text
    assert calls[0]['input_bindings']=={'source':'typed'}
    assert set(calls[0])=={'name','workflow','auto_repair','input_bindings'}
    snap=u.snapshot(store,'alice',pid,'run1')
    assert snap['input_bindings']=={'source':'typed'}
    assert snap['bound_inputs']['source']['record']==record
    assert snap['bound_inputs']['source']['provenance'][0]['sha256']=='original'


@pytest.mark.parametrize('change',['foreign_user','foreign_project','not_ready','wrong_root','missing','mixed','wrong_type'])
def test_invalid_binding_never_reaches_engine(bound,change):
    store,c,pid,calls,record=bound;request=body();headers={}
    if change=='foreign_user':headers={'x-user':'bob'}
    if change=='foreign_project':pid=store.create('other','','alice')['id']
    if change=='not_ready':
        with store.connect() as db:db.execute("UPDATE input_dataset_imports SET status='validating'")
    if change=='wrong_root':request['input_bindings']={'unknown':'typed'}
    if change=='missing':request['input_bindings']={}
    if change=='mixed':request['dataset_id']='legacy'
    if change=='wrong_type':
        record['contract']['data_type']='contigs'
        with store.connect() as db:db.execute('UPDATE input_dataset_imports SET record_json=?',(json.dumps(record),))
    response=c.post(f'/api/projects/{pid}/analysis/workflows/start',json=request,headers=headers)
    assert response.status_code in (404,409,422),response.text
    assert not calls
