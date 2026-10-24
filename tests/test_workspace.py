import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.bioflow_platform import app as gateway, auth, assets, workspace
from backend.bioflow_platform.store import Store


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(gateway,'store',Store(tmp_path/'workspace.sqlite3'))
    monkeypatch.setattr(assets,'ROOT',tmp_path)
    monkeypatch.setattr(workspace,'ROOT',tmp_path)
    c=TestClient(gateway.app)
    r=c.post('/api/auth/register',json={'username':'owner','password':'developer'})
    c.headers['X-BioFlow-CSRF']=r.json()['user']['csrf']
    c.uid=r.json()['user']['id']
    c.pid=c.post('/api/projects',json={'name':'Study'}).json()['id']
    return c


def test_change_password_revokes_previous_session(client):
    old=TestClient(gateway.app)
    old.post('/api/auth/login',json={'username':'owner','password':'developer'})
    bad=client.post('/api/auth/password',json={'current_password':'bad','new_password':'changed123'})
    assert bad.status_code==400
    changed=client.post('/api/auth/password',json={'current_password':'developer','new_password':'changed123'})
    assert changed.status_code==200
    client.headers['X-BioFlow-CSRF']=changed.json()['user']['csrf']
    assert old.get('/api/projects').status_code==401
    assert client.get('/api/projects').status_code==200
    assert client.post('/api/auth/login',json={'username':'owner','password':'developer'}).status_code==401
    assert client.post('/api/auth/login',json={'username':'owner','password':'changed123'}).status_code==200


def test_upload_classification_download_and_isolation(client,monkeypatch):
    calls=[]
    async def kb(self,pid,user,headers=None):
        gateway.store.bind_kb(pid,'kb-study');return 'kb-study'
    async def send(self,service,path,stored_path,name,headers=None):
        calls.append((service,path,name));return {'id':'document-1'}
    monkeypatch.setattr(assets.AssetService,'ensure_kb',kb)
    monkeypatch.setattr(assets.AssetService,'send_file',send)
    base=f'/api/projects/{client.pid}/assets'
    assert client.post(base,files={'file':('empty.csv',b'')}).status_code==422
    doc=client.post(base,files={'file':('paper.pdf',b'%PDF-content')})
    assert doc.status_code==201 and doc.json()['kind']=='literature'
    assert calls[0][1]=='/api/knowledge-bases/kb-study/documents'
    data=client.post(base,files={'file':('table.csv',b'sample,value\na,1\n')}).json()
    assert data['kind']=='data'
    assert client.get(base+'/'+data['id']+'/download').content==b'sample,value\na,1\n'
    assert len(client.get(base).json()['items'])==2
    other=TestClient(gateway.app)
    other.post('/api/auth/register',json={'username':'other','password':'developer'})
    assert other.get(base).status_code==404
    assert other.get(base+'/'+data['id']+'/download').status_code==404


def test_qwen_command_waits_for_missing_model_and_retries(client,monkeypatch):
    original=httpx.AsyncClient
    def offline(req):raise httpx.ConnectError('offline',request=req)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(offline),**kw))
    base=f'/api/projects/{client.pid}/assistant'
    r=client.post(base,json={'instruction':'分析上传的样本'})
    assert r.status_code==202
    run=client.get(base+'/'+r.json()['id']).json()
    assert run['status']=='waiting_model'
    assert run['instruction']=='分析上传的样本'
    assert client.post(base+'/'+run['id']+'/retry',json={}).status_code==202
    assert client.post(base,json={'instruction':'test','model':'qwen3:4b'}).status_code==422


def test_qwen_analysis_dispatch_is_scoped_and_records_task(client,monkeypatch):
    original=httpx.AsyncClient;requests=[]
    def response(req):
        requests.append((req.url.path,json.loads(req.content)))
        if req.url.path=='/api/chat':return httpx.Response(200,json={'message':{'content':'{"mode":"analysis","summary":"运行宏基因组分析"}'}})
        if req.url.path=='/api/tasks':return httpx.Response(200,json={'id':'new-task'})
        raise AssertionError(str(req.url))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(response),**kw))
    async def manifest(self,pid,user):
        assert pid==client.pid and user['id']==client.uid
        return {'manifest_path':'/owned/manifest.csv','dataset_id':'a'*32}
    monkeypatch.setattr(assets.AssetService,'manifest',manifest)
    base=f'/api/projects/{client.pid}/assistant'
    r=client.post(base,json={'instruction':'进行质控和物种分析'})
    run=client.get(base+'/'+r.json()['id']).json()
    assert run['status']=='completed'
    assert gateway.store.ids(client.pid,'task')==['new-task']
    assert gateway.store.owns('task','new-task',client.uid)
    assert requests[0][1]['model']=='qwen3:14b'
    assert requests[1][1]['manifest_path']=='/owned/manifest.csv'


def test_classify_and_hash_validation(tmp_path):
    assert assets.classify('paper.PDF')=='literature'
    assert assets.classify('samples.txt',b'id\tx\ty')=='data'
    assert assets.classify('reads_R1.fastq.gz')=='data'
    f=tmp_path/'file';f.write_bytes(b'content')
    assert assets.availability(f,'wrong')[0]=='integrity_error'
    f.write_bytes(b'')
    assert assets.availability(f)[0]=='empty'


def test_artifact_preview_checks_project_task_and_file_hash(client,tmp_path):
    import sqlite3,hashlib
    folder=tmp_path/'BioLLM_RawDataAnalysis/runtime'
    (folder/'state').mkdir(parents=True)
    output=folder/'outputs/task-a/chart.png';output.parent.mkdir(parents=True)
    output.write_bytes(b'\x89PNG\r\n\x1a\nactual-chart')
    assets.write_snapshot('task-a',{'id':'task-a'},[{'artifact_id':'figure-a','task_id':'task-a','status':'active',
        'path':'/server/runtime/outputs/task-a/chart.png','sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'media_type':'image/png'}])
    gateway.store.claim('task','task-a',client.uid);gateway.store.link(client.pid,'task','task-a')
    url=f'/api/projects/{client.pid}/artifacts/figure-a/preview'
    r=client.get(url)
    assert r.status_code==200 and r.content==output.read_bytes()
    assert r.headers['content-type']=='image/png'
    p2=client.post('/api/projects',json={'name':'Unrelated'}).json()['id']
    assert client.get(f'/api/projects/{p2}/artifacts/figure-a/preview').status_code==404
    output.write_bytes(b'tampered')
    assert client.get(url).status_code==409


def test_api_catalogue_matches_actual_hash_not_just_filename(tmp_path,monkeypatch):
    import hashlib
    monkeypatch.setattr(assets,'ROOT',tmp_path)
    folder=tmp_path/'BioLLM_RawDataAnalysis/runtime/outputs/task-a'
    (folder/'v1').mkdir(parents=True);(folder/'v2').mkdir()
    (folder/'v1/chart.png').write_bytes(b'old')
    correct=folder/'v2/chart.png';correct.write_bytes(b'current')
    records=[{'artifact_id':'a','file_name':'chart.png','sha256':hashlib.sha256(b'current').hexdigest()}]
    assert assets.resolve_catalogue_paths('task-a',records)[0]['path']==str(correct)
