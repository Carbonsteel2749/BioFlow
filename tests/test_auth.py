import httpx
import pytest
from fastapi.testclient import TestClient
from backend.bioflow_platform import app as gateway,auth
from backend.bioflow_platform.store import Store


@pytest.fixture
def store(tmp_path,monkeypatch):
    store=Store(tmp_path/'auth.sqlite3')
    monkeypatch.setattr(gateway,'store',store)
    return store


def account(name):
    c=TestClient(gateway.app)
    r=c.post('/api/auth/register',json={'username':name,'password':'Test-password-2026!','role':'developer'})
    assert r.status_code==201
    c.headers['X-BioFlow-CSRF']=r.json()['user']['csrf']
    return c,r.json()['user']


def test_guest_apis_are_locked(store):
    c=TestClient(gateway.app)
    assert c.get('/').status_code==200
    assert c.get('/api/auth/me').json()=={'user':None}
    assert c.get('/api/auth/me').headers['cache-control']=='no-store'
    for path in ['/api/projects','/api/skills','/api/models','/api/status','/services/analysis/api/tasks','/services/writing/api/sessions','/docs','/openapi.json']:
        assert c.get(path).status_code==401


def test_registration_password_cookie_and_logout(store):
    c,user=account('researcher')
    assert user['role']=='user'
    assert any(key.lower()=='httponly' for key in c.cookies.jar._cookies['testserver.local']['/'][auth.COOKIE]._rest)
    with store.connect() as db:
        hashed=db.execute('SELECT password_hash FROM users WHERE id=?',(user['id'],)).fetchone()[0]
    assert 'Test-password' not in hashed
    assert auth.verify_password('Test-password-2026!',hashed)
    assert c.post('/api/auth/logout',json={}).status_code==200
    assert c.get('/api/projects').status_code==401
    assert c.post('/api/auth/login',json={'username':'researcher','password':'wrong-password'}).status_code==401
    assert c.post('/api/auth/login',json={'username':'researcher','password':'Test-password-2026!'}).status_code==200


def test_accounts_cannot_read_or_link_each_others_records(store):
    a,ua=account('alice');b,ub=account('bob')
    pid=a.post('/api/projects',json={'name':'Alice Project'}).json()['id']
    assert len(a.get('/api/projects').json())==1
    assert b.get('/api/projects').json()==[]
    assert b.get(f'/api/projects/{pid}/history').status_code==404
    store.claim('task','alice-task',ua['id'])
    assert b.get('/services/analysis/api/tasks/alice-task').status_code==404
    assert b.post('/services/writing/api/sessions',json={'analysis_task_id':'alice-task','analysis_url':'http://127.0.0.1:8000'}).status_code==404
    assert b.put('/services/writing/api/llm-config',json={'model':'other'}).status_code==403
    assert b.get('/services/literature/api/config').status_code==403


def test_csrf_and_login_rate_limit(store):
    c,user=account('csrfuser')
    del c.headers['X-BioFlow-CSRF']
    assert c.post('/api/projects',json={'name':'Blocked'}).status_code==403
    guest=TestClient(gateway.app)
    for _ in range(8):
        assert guest.post('/api/auth/login',json={'username':'absent','password':'incorrect-password'}).status_code==401
    assert guest.post('/api/auth/login',json={'username':'absent','password':'incorrect-password'}).status_code==429


def test_list_filter_and_creation_ownership(store,monkeypatch):
    c,user=account('owner')
    store.claim('task','mine',user['id'])
    original=httpx.AsyncClient
    async def handler(request):
        if request.method=='GET':
            return httpx.Response(200,json=[{'id':'mine'},{'id':'another-user'}])
        return httpx.Response(201,json={'id':'new-upload'})
    monkeypatch.setattr(gateway.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    assert c.get('/services/analysis/api/tasks').json()==[{'id':'mine'}]
    assert c.post('/services/analysis/api/uploads/files',files={'file':('a.fq',b'@a\nA\n+\nI\n')}).status_code==201
    assert store.owns('upload','new-upload',user['id'])
