import copy
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.bioflow_platform import app as gateway
from backend.bioflow_platform.store import Store
from backend.bioflow_platform.sample_groups import install_groups
from fastapi import APIRouter, FastAPI, Request


@pytest.fixture
def group_client(tmp_path):
    store=Store(tmp_path/'groups.sqlite3')
    store.claim('dataset','study','owner')
    store.link('project','dataset','study')
    state={'version':0,'rows':[{'sample_id':'A','group':''},{'sample_id':'B','group':''}]}
    async def call(service,path,method='GET',payload=None):
        if method=='PUT':
            if payload['expected_version']!=state['version']:raise HTTPException(409,'conflict')
            state.update(version=state['version']+1,rows=copy.deepcopy(payload['rows']))
        return copy.deepcopy(state)
    app=FastAPI()
    @app.middleware('http')
    async def user(request,call_next):
        request.state.user={'id':request.headers.get('x-owner','owner')}
        return await call_next(request)
    def check(pid,request):
        if pid!='project':raise HTTPException(404,'project')
    router=APIRouter(prefix='/api/projects/{pid}')
    install_groups(router,lambda:store,check,call);app.include_router(router)
    client=TestClient(app);client.base='/api/projects/project/datasets/study/sample-groups';client.upstream=state
    return client


def test_groups_batches_revision_and_conflict(group_client):
    c=group_client;initial=c.get(c.base).json();assert initial['version']=='0:0'
    rows=[{'sample_id':'A','group':'自定义对照','batch':'批次甲'},{'sample_id':'B','group':'实验组','batch':''}]
    saved=c.put(c.base,json={'version':initial['version'],'rows':rows});assert saved.status_code==200,saved.text
    assert saved.json()['version']=='1:1' and saved.json()['rows']==rows
    stale=c.put(c.base,json={'version':'0:0','rows':rows});assert stale.status_code==409
    assert stale.json()['current']['rows']==rows
    assert c.get(c.base,headers={'x-owner':'stranger'}).status_code==404
    assert c.get(c.base.replace('project/datasets','other/datasets')).status_code==404
    c.upstream.update(version=2,rows=[{'sample_id':'A','group':'外部修改'},{'sample_id':'B','group':'实验组'}])
    assert c.put(c.base,json={'version':'1:1','rows':rows}).status_code==409
    assert c.get(c.base).json()['rows'][0]['batch']=='批次甲'


def test_import_mapping_and_validation(group_client):
    c=group_client
    body={'text':'编号,处理,批号\nA,对照组,批次1\nB,实验组,批次2\n','mapping':{'sample_id':'编号','group':'处理','batch':'批号'}}
    result=c.post(c.base+'/preview',json=body);assert result.status_code==200
    assert result.json()['valid'] and result.json()['rows'][1]['batch']=='批次2'
    assert c.get(c.base).json()['version']=='0:0'
    body['text']='编号,处理,批号\nA,对照组,x\nA,,y\nX,未知,z\n'
    errors=c.post(c.base+'/preview',json=body).json();assert not errors['valid']
    assert len(errors['errors'])==4
    body={'text':'sample_id\tgroup\nA\t"quoted, group"\nB\t实验组','delimiter':'\t','mapping':{'sample_id':'sample_id','group':'group','batch':''}}
    assert c.post(c.base+'/preview',json=body).json()['valid']
    assert c.put(c.base,json={'version':'0:0','rows':[{'sample_id':'A','group':'','batch':''}]}).status_code==422
    body['mapping']['group']='sample_id'
    assert c.post(c.base+'/preview',json=body).status_code==422
