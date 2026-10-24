import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from backend.bioflow_platform import app as gateway,assets,workspace
from backend.bioflow_platform.store import Store


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(gateway,'store',Store(tmp_path/'portal.sqlite3'));monkeypatch.setattr(assets,'ROOT',tmp_path);monkeypatch.setattr(workspace,'ROOT',tmp_path)
    c=TestClient(gateway.app);r=c.post('/api/auth/register',json={'username':'catalogue','password':'developer'});c.headers['X-BioFlow-CSRF']=r.json()['user']['csrf'];c.uid=r.json()['user']['id'];c.pid=c.post('/api/projects',json={'name':'Study'}).json()['id'];c.base=f'/api/projects/{c.pid}';return c


def test_metadata_page_does_not_read_or_hash_files(client,tmp_path,monkeypatch):
    c=client;task='task-a';gateway.store.claim('task',task,c.uid);gateway.store.link(c.pid,'task',task)
    records=[{'artifact_id':str(i),'task_id':task,'status':'active','artifact_type':'figure.png','path':f'/server/runtime/outputs/{task}/{i}.png','sha256':'unused','size_bytes':123,'media_type':'image/png'} for i in range(55)]
    assets.write_snapshot(task,{'id':task,'name':'Study'},records)
    def forbidden(*args,**kwargs):raise AssertionError('Listing must not inspect file bytes')
    monkeypatch.setattr(assets,'availability',forbidden);monkeypatch.setattr(assets,'resolve_catalogue_paths',forbidden)
    result=c.get(c.base+'/assets?kind=charts&limit=24&offset=24');assert result.status_code==200,result.text
    data=result.json();assert len(data['items'])==24 and data['counts']['charts']==55 and data['total']==55
    assert all(r['status']=='not_checked' and not r['relative_path'].startswith('/') for r in data['items'])
    snapshot=assets.read_snapshot(task);assert not snapshot['artifacts'][0]['path'].startswith('/')
    p2=c.post('/api/projects',json={'name':'Other'}).json()['id']
    assert c.get(f'/api/projects/{p2}/assets?kind=charts').json()['items']==[]


def test_api_metadata_without_absolute_file_path_keeps_name(client):
    c=client;gateway.store.claim('task','task-b',c.uid);gateway.store.link(c.pid,'task','task-b')
    assets.write_snapshot('task-b',{'id':'task-b','name':'Study'},[{'artifact_id':'chart','task_id':'task-b','status':'active','artifact_type':'figure.qc','file_name':'counts.png','sha256':'none','size_bytes':128,'media_type':'image/png'}])
    row=c.get(c.base+'/assets?kind=charts').json()['items'][0]
    assert row['name']=='counts.png' and row['relative_path'].endswith('/counts.png')


def test_relative_upload_survives_listing_and_download(client,tmp_path):
    c=client;data=b'sample,value\nA,10\n';folder=tmp_path/'runtime/assets'/c.uid/c.pid;folder.mkdir(parents=True)
    (folder/'file.csv').write_bytes(data)
    with gateway.store.connect() as db:
        db.execute('INSERT INTO assets(id,project_id,user_id,name,kind,path,size_bytes,sha256,status) VALUES(?,?,?,?,?,?,?,?,?)',('upload',c.pid,c.uid,'file.csv','data',f'{c.uid}/{c.pid}/file.csv',len(data),hashlib.sha256(data).hexdigest(),'ready'))
    rows=c.get(c.base+'/assets?kind=data&limit=24').json()['items'];assert len(rows)==1
    assert rows[0]['relative_path']==f'{c.uid}/{c.pid}/file.csv'
    assert c.get(rows[0]['url']).content==data
    with gateway.store.connect() as db:db.execute('UPDATE assets SET path=? WHERE id=?',('../../private.csv','upload'))
    assert c.get(rows[0]['url']).status_code==409


def test_dataset_summary_is_listed_before_backend_refresh(client):
    c=client;gateway.store.claim('dataset','study',c.uid,{'dataset_id':'study','name':'SRR cohort'});gateway.store.link(c.pid,'dataset','study')
    data=c.get(c.base+'/assets?kind=data&limit=24').json()
    assert data['datasets'][0]['name']=='SRR cohort' and data['datasets'][0]['status']=='not_checked'
