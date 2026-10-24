import hashlib
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.bioflow_platform import app as gateway,assets,workspace,project_figures
from backend.bioflow_platform.store import Store

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(gateway,'store',Store(tmp_path/'portal.sqlite3'));monkeypatch.setattr(assets,'ROOT',tmp_path);monkeypatch.setattr(workspace,'ROOT',tmp_path)
    c=TestClient(gateway.app);r=c.post('/api/auth/register',json={'username':'researcher','password':'developer'})
    c.uid=r.json()['user']['id'];c.headers['X-BioFlow-CSRF']=r.json()['user']['csrf'];c.pid=c.post('/api/projects',json={'name':'Study'}).json()['id'];c.base=f'/api/projects/{c.pid}'
    async def kb(self,pid,user,headers=None):gateway.store.bind_kb(pid,'kb-'+pid);return 'kb-'+pid
    async def send(self,service,path,stored_path,name,headers=None):return {'id':'doc-'+hashlib.md5(path.encode()).hexdigest(),'status':'processing'}
    monkeypatch.setattr(assets.AssetService,'ensure_kb',kb);monkeypatch.setattr(assets.AssetService,'send_file',send)
    return c

def upload(c,name,data,purpose):
    r=c.post(c.base+'/assets?purpose='+purpose,files={'file':(name,data)});assert r.status_code==201,r.text;return r.json()

def test_explicit_pdf_report_and_word_draft_have_distinct_purpose(client):
    report=upload(client,'report.pdf',b'%PDF-external-report','results');draft=upload(client,'paper.docx',b'word-draft','drafts')
    assert report['kind']=='results' and report['metadata']['origin']=='external_import'
    assert draft['kind']=='drafts'
    assert gateway.store.get(client.pid)['knowledge_base_id']==''
    assert client.post(client.base+'/assets?purpose=results',files={'file':('x.fastq',b'@a\nA\n+\n!\n')}).status_code==422

def test_purpose_survives_chunk_session_resume(client):
    data=b'gene,log2fc,pvalue\nG1,2,0.01\n';chunk=16
    r=client.post(client.base+'/uploads/drafts',json={'name':'external.csv','size_bytes':len(data),'purpose':'results','details':{'software':'DESeq2','result_type':'differential'}});uid=r.json()['id']
    hashes=[hashlib.sha256(data[i:i+chunk]).hexdigest() for i in range(0,len(data),chunk)]
    fp=hashlib.sha256(f"{len(data)}:{chunk}:{':'.join(hashes)}".encode()).hexdigest()
    body={'draft_id':uid,'name':'external.csv','size_bytes':len(data),'chunk_bytes':chunk,'hashes':hashes,'fingerprint':fp}
    assert client.post(client.base+'/uploads',json=body).json()['purpose']=='results'
    for i in range(len(hashes)):assert client.put(client.base+f'/uploads/{uid}/chunks/{i}',content=data[i*chunk:(i+1)*chunk]).status_code==200
    final=client.post(client.base+f'/uploads/{uid}/complete',json={});assert final.status_code==200,final.text
    rows=client.get(client.base+'/assets?kind=results').json()['items'];assert rows[0]['metadata']['software']=='DESeq2'

def test_personal_paper_reuse_keeps_account_file_and_project_index_states(client,monkeypatch):
    paper=upload(client,'paper.pdf',b'%PDF-paper','literature');identifier=paper['metadata']['personal_document_id']
    otherpid=client.post('/api/projects',json={'name':'Other Study'}).json()['id']
    r=client.post(f'/api/projects/{otherpid}/literature/{identifier}/link',json={});assert r.status_code==200,r.text
    assert client.post(f'/api/projects/{otherpid}/literature/{identifier}/link',json={}).json()['already_linked']
    papers=client.get('/api/library/literature').json()['items'];assert len(papers)==1 and len(papers[0]['projects'])==2
    assert all(p['index_status']=='processing' for p in papers[0]['projects'])
    assert client.patch('/api/library/literature/'+identifier,json={'title':'Edited title','year':'2026','doi':'10.1000/test'}).status_code==200
    assert client.get('/api/library/literature/'+identifier+'/download').content==b'%PDF-paper'
    other=TestClient(gateway.app);other.post('/api/auth/register',json={'username':'stranger','password':'developer'})
    assert other.get('/api/library/literature/'+identifier+'/download').status_code==404
    assert other.get('/api/library/literature').json()['items']==[]

def fake_render(directory):
    spec=json.loads((directory/'request.json').read_text());outputs={}
    for ext in ('svg','pdf','png'):
        content=b'<svg xmlns="http://www.w3.org/2000/svg"><text>preview</text></svg>' if ext=='svg' else b'test-output'
        (directory/('figure.'+ext)).write_bytes(content);outputs[ext]={'sha256':hashlib.sha256(content).hexdigest(),'size_bytes':len(content)}
    return {'outputs':outputs,'legend':[{'id':'group:A','label':'A'}],'defaults':{'title':'PCA','x_label':'PC1','y_label':'PC2'}}

def test_chart_preview_versions_snapshot_save_and_isolation(client,monkeypatch):
    monkeypatch.setattr(project_figures,'run_renderer',fake_render)
    source=upload(client,'matrix.csv',b'sample,f1,f2\nS1,1,2\nS2,2,1\n','data')
    response=client.post(client.base+'/figures',json={'chart':'pca','source_id':source['id']});assert response.status_code==202,response.text
    fid=response.json()['id'];first=client.get(client.base+'/figures/'+fid).json();assert first['status']=='completed' and not first['saved']
    with gateway.store.connect() as db:row=dict(db.execute('SELECT * FROM assets WHERE id=?',(source['id'],)).fetchone())
    assets.uploaded_path(row,client.pid,client.uid).write_text('changed')
    response=client.post(client.base+'/figures',json={'chart':'pca','parent_id':fid,'parameters':{'title':'Updated','legend_labels':{'group:A':'Treatment'}}});assert response.status_code==202
    child=response.json()['id'];second=client.get(client.base+'/figures/'+child).json();assert second['parent_id']==fid and second['source']['sha256']==first['source']['sha256']
    assert client.post(client.base+'/figures/'+child+'/save',json={}).status_code==200
    listed=client.get(client.base+'/assets?kind=charts').json()['items'];assert any(a.get('figure_id')==child for a in listed)
    response=client.get(client.base+'/figures/'+child+'/file/svg');assert response.status_code==200 and 'sandbox' in response.headers['content-security-policy']
    other=TestClient(gateway.app);other.post('/api/auth/register',json={'username':'outsider','password':'developer'})
    assert other.get(client.base+'/figures/'+fid).status_code==404
    assert client.get(client.base+'/figures/'+fid).json()['parameters']['title'] is None

def test_disabled_ability_is_enforced_on_backend(client,monkeypatch):
    with gateway.store.connect() as db:db.execute("UPDATE users SET role='developer' WHERE id=?",(client.uid,))
    response=client.patch('/api/library/abilities/skills/chart:pca',json={'enabled':False,'category':'visualization'});assert response.status_code==200,response.text
    assert client.post(client.base+'/figures',json={'chart':'pca','source_id':'anything'}).status_code==409
    assert client.patch('/api/library/abilities/skills/not-a-skill',json={'enabled':False}).status_code==404

def test_unlink_pending_paper_keeps_personal_file_and_other_catalogue_rows(client,monkeypatch):
    from fastapi import HTTPException
    from backend.bioflow_platform import catalogue
    async def offline(*args,**kwargs):raise HTTPException(503,'offline')
    monkeypatch.setattr(assets.AssetService,'send_file',offline)
    paper=upload(client,'pending.pdf',b'%PDF-pending','literature');identifier=paper['metadata']['personal_document_id']
    catalogue.put(gateway.store,client.pid,client.uid,'tasks',[{'id':'old-chart','kind':'charts','name':'Keep me','status':'ready'}])
    assert client.post(client.base+'/literature/'+identifier+'/unlink',json={}).status_code==200
    with gateway.store.connect() as db:assert db.execute('SELECT count(*) FROM catalogue_entries WHERE id=?',('old-chart',)).fetchone()[0]==1
    assert client.get('/api/library/literature/'+identifier+'/download').content==b'%PDF-pending'
    assert client.get('/api/library/literature').json()['items'][0]['projects']==[]

def test_generated_task_artifact_is_a_valid_protected_chart_source(client,monkeypatch):
    monkeypatch.setattr(project_figures,'run_renderer',fake_render)
    tid='owned-task';aid='owned-table';content=b'sample,f1,f2\nA,1,2\nB,2,1\n'
    path=assets.analysis_root()/'runtime/outputs'/tid/'table.tsv';path.parent.mkdir(parents=True);path.write_bytes(content)
    gateway.store.claim('task',tid,client.uid);gateway.store.link(client.pid,'task',tid)
    assets.write_snapshot(tid,{'id':tid,'name':'Original analysis'},[{'artifact_id':aid,'task_id':tid,'status':'active','artifact_type':'table','path':str(path),'file_name':'table.tsv','sha256':hashlib.sha256(content).hexdigest(),'size_bytes':len(content),'media_type':'text/tab-separated-values'}])
    assert any(a['id']=='artifact:'+aid for a in client.get(client.base+'/figure-sources').json()['items'])
    response=client.post(client.base+'/figures',json={'chart':'pca','source_id':'artifact:'+aid});assert response.status_code==202,response.text
    otherpid=client.post('/api/projects',json={'name':'Unrelated'}).json()['id']
    assert client.post(f'/api/projects/{otherpid}/figures',json={'chart':'pca','source_id':'artifact:'+aid}).status_code==404

def test_failed_document_reindexes_existing_record(client,monkeypatch):
    import httpx
    paper=upload(client,'paper.pdf',b'%PDF-reindex','literature')
    with gateway.store.connect() as db:db.execute("UPDATE assets SET status='failed' WHERE id=?",(paper['id'],))
    original=httpx.AsyncClient;calls=[]
    def handler(request):
        calls.append(request.url.path);assert request.url.path.endswith('/reindex')
        return httpx.Response(200,json={'document':{'id':paper['metadata']['document_id'],'status':'processing'}})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler)))
    response=client.post(client.base+'/assets/'+paper['id']+'/retry',json={});assert response.status_code==200,response.text
    assert len(calls)==1
    with gateway.store.connect() as db:row=db.execute('SELECT status,metadata FROM assets WHERE id=?',(paper['id'],)).fetchone()
    assert row['status']=='indexing' and json.loads(row['metadata'])['personal_document_id']==paper['metadata']['personal_document_id']

def test_disabled_model_blocks_actual_section_chat_and_embedding_import(client):
    with gateway.store.connect() as db:
        db.execute("UPDATE users SET role='developer' WHERE id=?",(client.uid,))
    gateway.store.claim('session','session-one',client.uid)
    assert client.patch('/api/library/abilities/models/writing-model',json={'enabled':False}).status_code==200
    response=client.post('/services/writing/api/sessions/session-one/sections/introduction/chat',json={'message':'hello'})
    assert response.status_code==409,response.text
    client.patch('/api/library/abilities/models/literature-embedding',json={'enabled':False})
    paper=upload(client,'saved-only.pdf',b'%PDF-no-embedding','literature')
    assert paper['status']=='stored' and '停用' in str(paper['metadata']['upstream_error'])

def test_queued_chart_rechecks_ability_before_rendering(client,monkeypatch):
    calls=[]
    class StopBeforeSlot:
        async def __aenter__(self):
            with gateway.store.connect() as db:db.execute("INSERT INTO ability_settings(kind,id,enabled) VALUES('skills','chart:pca',0)")
        async def __aexit__(self,*args):pass
    monkeypatch.setattr(project_figures,'SLOTS',StopBeforeSlot());monkeypatch.setattr(project_figures,'run_renderer',lambda p:calls.append(p))
    source=upload(client,'matrix.csv',b'sample,f1,f2\nA,1,2\nB,2,1\n','data')
    response=client.post(client.base+'/figures',json={'chart':'pca','source_id':source['id']});assert response.status_code==202
    detail=client.get(client.base+'/figures/'+response.json()['id']).json()
    assert detail['status']=='failed' and '已停用' in detail['error'] and calls==[]

def test_registered_chart_skill_dispatches_to_same_versioned_program(client,monkeypatch):
    monkeypatch.setattr(project_figures,'run_renderer',fake_render)
    source=upload(client,'matrix.csv',b'sample,f1,f2\nA,1,2\nB,2,1\n','data')
    response=client.post(client.base+'/skills/chart:pca',json={'version':'1.0.0','payload':{'source_id':source['id']}})
    assert response.status_code==200,response.text
    fid=response.json()['result']['id'];assert client.get(client.base+'/figures/'+fid).json()['status']=='completed'
    assert client.post(client.base+'/skills/chart:pca',json={'version':'99','payload':{'source_id':source['id']}}).status_code==409

def test_project_edit_delete_and_owner_isolation(client):
    r=client.patch(client.base,json={'name':'Renamed','description':'Updated description'})
    assert r.status_code==200 and r.json()['name']=='Renamed'
    assert client.patch(client.base,json={'name':'   '}).status_code==422
    stranger=TestClient(gateway.app)
    login=stranger.post('/api/auth/register',json={'username':'another_user','password':'developer'}).json()
    stranger.headers['X-BioFlow-CSRF']=login['user']['csrf']
    assert stranger.patch(client.base,json={'name':'Forbidden'}).status_code==404
    assert stranger.delete(client.base).status_code==404
    assert client.delete(client.base).status_code==200
    assert client.get('/api/projects').json()==[]
    assert gateway.store.get(client.pid)['archived']==1
    assert client.get(client.base+'/history').status_code==404
