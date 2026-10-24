from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.bioflow_platform import app as gateway
from backend.bioflow_platform.store import Store
from backend.bioflow_platform import auth


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(gateway,'store',Store(tmp_path/'projects.sqlite3'))
    user=auth.create_user(gateway.store,'testdeveloper','Test-password-2026!','developer')
    client=TestClient(gateway.app)
    login=client.post('/api/auth/login',json={'username':'testdeveloper','password':'Test-password-2026!'})
    client.headers['X-BioFlow-CSRF']=login.json()['user']['csrf']
    client.user_id=user['id']
    return client


def test_frontend_and_project_persistence(client):
    html=client.get('/')
    assert html.status_code==200
    assert '/static/app.js' in html.text
    assert '示例数据，未连接实际算法' not in html.text
    assert client.get('/static/theme.css').status_code==200
    assert client.post('/api/projects',json={'name':'  '}).status_code==422
    created=client.post('/api/projects',json={'name':'研究A','description':'测试'}).json()
    assert client.get('/api/projects').json()[0]['id']==created['id']
    reopened=Store(gateway.store.path)
    assert reopened.get(created['id'])['name']=='研究A'
    assert len(client.get(f"/api/projects/{created['id']}/history").json())==1


def test_links_are_project_scoped(client,monkeypatch):
    async def mock(*args,**kwargs):
        return {'id':'task_1'}
    monkeypatch.setattr(gateway,'upstream',mock)
    gateway.store.claim('task','task_1',client.user_id)
    a=client.post('/api/projects',json={'name':'A'}).json()['id']
    b=client.post('/api/projects',json={'name':'B'}).json()['id']
    assert client.post(f'/api/projects/{a}/links/task',json={'id':'task_1'}).status_code==200
    assert client.get(f'/api/projects/{a}/links/task').json()==['task_1']
    assert client.get(f'/api/projects/{b}/links/task').json()==[]
    assert client.get('/api/projects/missing/links/task').status_code==404
    assert client.post(f'/api/projects/{a}/links/task',json={'id':'../bad'}).status_code==422


def test_real_skill_catalog_and_execution(client):
    catalog=client.get('/api/skills').json()
    assert len(catalog)>=20
    skill=next(s for s in catalog if s['name']=='nature-writing')
    pid=client.post('/api/projects',json={'name':'Skills'}).json()['id']
    r=client.post(f'/api/projects/{pid}/skills/nature-writing',json={'payload':{'task_context':'起草方法'},'version':skill['version']})
    assert r.status_code==200
    assert r.json()['skill']=='nature-writing'
    assert r.json()['status']=='skipped'
    assert r.json()['data']['execution_mode']=='agent'
    assert client.get(f'/api/projects/{pid}/history').json()[0]['action']=='执行 Skill'


def test_proxy_streams_uploads_errors_and_downloads(client,monkeypatch):
    gateway.store.claim('task','task_1',client.user_id)
    original=httpx.AsyncClient
    captured=[]
    async def handler(request):
        data=await request.aread()
        captured.append((str(request.url),data,request.headers))
        if request.url.path.endswith('/download'):
            return httpx.Response(200,content=b'PK-file',headers={'content-type':'application/zip','content-disposition':'attachment; filename=result.zip'})
        if request.url.path=='/api/artifacts/result':
            return httpx.Response(200,json={'task_id':'task_1'})
        if request.url.path.endswith('/login'):
            return httpx.Response(200,json={'ok':True},headers={'set-cookie':'ai_localbase_csrf=abc; Path=/'})
        return httpx.Response(422,json={'detail':'原始校验错误'})
    monkeypatch.setattr(gateway.httpx,'AsyncClient',lambda **kwargs: original(transport=httpx.MockTransport(handler),**kwargs))
    r=client.post('/services/analysis/api/uploads/files',files={'file':('sample.fastq',b'@a\nA\n+\nI\n')})
    assert r.status_code==422 and r.json()['detail']=='原始校验错误'
    assert b'@a\nA\n+\nI\n' in captured[-1][1]
    r=client.get('/services/analysis/api/artifacts/result/download')
    assert r.content==b'PK-file' and 'result.zip' in r.headers['content-disposition']
    r=client.post('/services/literature/api/auth/login',json={'password':'test'})
    assert 'ai_localbase_csrf=abc' in r.headers['set-cookie']
    assert client.get('/services/unknown/api/tasks').status_code==404
    assert client.get('/services/analysis/static/index.html').status_code==404


def test_cross_origin_write_rejected(client):
    assert client.post('/api/projects',json={'name':'A'},headers={'Origin':'https://other.example'}).status_code==403
