import hashlib
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.bioflow_platform import app as gateway,assets,workspace
from backend.bioflow_platform.store import Store


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(gateway,'store',Store(tmp_path/'portal.sqlite3'))
    monkeypatch.setattr(assets,'ROOT',tmp_path)
    monkeypatch.setattr(workspace,'ROOT',tmp_path)
    c=TestClient(gateway.app)
    r=c.post('/api/auth/register',json={'username':'uploader','password':'developer'})
    c.headers['X-BioFlow-CSRF']=r.json()['user']['csrf'];c.uid=r.json()['user']['id']
    c.pid=c.post('/api/projects',json={'name':'Upload Study'}).json()['id']
    c.base=f'/api/projects/{c.pid}/uploads'
    return c


def manifest(name,data,chunk=8):
    hashes=[hashlib.sha256(data[i:i+chunk]).hexdigest() for i in range(0,len(data),chunk)]
    fp=hashlib.sha256(f"{len(data)}:{chunk}:{':'.join(hashes)}".encode()).hexdigest()
    return {'name':name,'size_bytes':len(data),'chunk_bytes':chunk,'hashes':hashes,'fingerprint':fp}


def create(client,name='values.csv',data=b'sample,value\na,12345\nb,3\n',chunk=8):
    body=manifest(name,data,chunk)
    draft=client.post(client.base+'/drafts',json={'name':name,'size_bytes':len(data)})
    assert draft.status_code==201
    body['draft_id']=draft.json()['id']
    r=client.post(client.base,json=body)
    assert r.status_code==201,r.text
    return r.json(),body


def test_durable_resume_integrity_and_idempotent_finalize(client):
    data=b'sample,value\na,12345\nb,3\n';row,body=create(client,data=data)
    path=client.base+'/'+row['id']
    assert client.put(path+'/chunks/0',content=b'wrongxxx').status_code==422
    assert client.put(path+'/chunks/0',content=b'x'*9).status_code==413
    assert client.put(path+'/chunks/0',content=data[:8]).json()['received_bytes']==8
    assert client.post(path+'/pause',json={}).json()['status']=='paused'
    assert client.put(path+'/chunks/1',content=data[8:16]).status_code==409
    # A new browser session sees the same server-owned upload metadata.
    reopened=TestClient(gateway.app)
    login=reopened.post('/api/auth/login',json={'username':'uploader','password':'developer'})
    reopened.headers['X-BioFlow-CSRF']=login.json()['user']['csrf']
    restored=reopened.get(path).json();assert restored['received_chunks']==[0]
    assert reopened.post(path+'/complete',json={}).status_code==409
    assert reopened.post(path+'/resume',json={}).status_code==200
    for i in range(1,len(body['hashes'])):assert reopened.put(path+'/chunks/'+str(i),content=data[i*8:(i+1)*8]).status_code==200
    final=reopened.post(path+'/complete',json={});assert final.status_code==200,final.text
    assert final.json()['status']=='completed'
    assert reopened.post(path+'/complete',json={}).json()['asset_id']==final.json()['asset_id']
    result=reopened.get(f'/api/projects/{client.pid}/assets').json()['items']
    assert len(result)==1 and result[0]['kind']=='data'
    assert reopened.get(result[0]['url']).content==data
    assert reopened.post(path+'/cancel',json={}).status_code==409


def test_scope_validation_and_prepare_cancel_resume(client):
    row,body=create(client);path=client.base+'/'+row['id']
    other=TestClient(gateway.app)
    login=other.post('/api/auth/register',json={'username':'other','password':'developer'});other.headers['X-BioFlow-CSRF']=login.json()['user']['csrf']
    assert other.get(path).status_code==404
    assert other.put(path+'/chunks/0',content=b'sample,v').status_code==404
    another=client.post('/api/projects',json={'name':'Unrelated'}).json()['id']
    assert client.get(f'/api/projects/{another}/uploads/'+row['id']).status_code==404
    assert client.post(client.base,json={**body,'fingerprint':'0'*64}).status_code==422
    draft=client.post(client.base+'/drafts',json={'name':'later.csv','size_bytes':10}).json()
    assert client.post(client.base+'/'+draft['id']+'/cancel',json={}).json()['status']=='cancelled'
    assert client.post(client.base+'/'+draft['id']+'/resume',json={}).json()['status']=='preparing'
    bad=manifest('../bad.csv',b'content')
    assert client.post(client.base,json=bad).status_code==422


def test_fastq_uses_backend_sessions_without_duplicate_copy_and_pairs(client,monkeypatch,tmp_path):
    original=httpx.AsyncClient;sessions={};counter=0;calls=[]
    def response(req):
        nonlocal counter
        path=req.url.path;calls.append((req.method,path))
        if path.endswith('/sessions/config'):return httpx.Response(200,json={'chunk_bytes':8,'max_file_bytes':250*1024**3,'parallel_files':2})
        if path=='/api/uploads/sessions':
            body=json.loads(req.content);counter+=1;sid=f'{counter:032x}'
            sessions[sid]={'id':sid,'token':'secret','received_chunks':[],'data':{},**body}
            return httpx.Response(201,json=sessions[sid])
        if path=='/api/uploads/manifests':
            body=json.loads(req.content);assert body['files'][0]['sample_id']=='sample_A'
            return httpx.Response(200,json={'dataset_id':'a'*32,'manifest_path':'/owned/samples.csv','sample_count':len(body['files'])})
        sid=path.split('/')[4];state=sessions[sid]
        assert req.headers['x-upload-token']=='secret'
        if '/chunks/' in path:
            index=int(path.rsplit('/',1)[1]);assert hashlib.sha256(req.content).hexdigest()==state['chunk_sha256'][index]
            state['data'][index]=req.content
            if index not in state['received_chunks']:state['received_chunks'].append(index)
        if path.endswith('/complete'):
            data=b''.join(state['data'][i] for i in sorted(state['data']))
            output=tmp_path/'BioLLM_RawDataAnalysis/runtime/incoming/uploads'/sid/'reads.fastq';output.parent.mkdir(parents=True);output.write_bytes(data)
            return httpx.Response(200,json={'id':sid,'size':len(data),'checksum':'sha256:'+hashlib.sha256(data).hexdigest(),'status':'validated'})
        return httpx.Response(200,json={k:v for k,v in state.items() if k!='data'})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(response),**kw))
    finished=[]
    for mate in (1,2):
        data=f'@sample/{mate}\nACGT\n+\nIIII\n'.encode()
        row,body=create(client,name=f'sample_A_R{mate}.fastq',data=data)
        assert 'token' not in row and 'backend_token' not in row
        path=client.base+'/'+row['id']
        for i in range(len(body['hashes'])):assert client.put(path+'/chunks/'+str(i),content=data[i*8:(i+1)*8]).status_code==200
        assert client.post(path+'/complete',json={}).status_code==200
        finished.append(row['id'])
    listing=client.get(client.base).json();assert len(listing['files'])==2
    # The completed assets point at existing analysis storage, not another copy.
    assert not list((tmp_path/'runtime/assets').rglob('*'))
    other_project=client.post('/api/projects',json={'name':'Unrelated'}).json()['id']
    unauthorized={'name':'bad scope','pairs':[{'sample_id':'sample_A','read1':finished[0],'read2':finished[1]}]}
    assert client.post(f'/api/projects/{other_project}/uploads/pairs',json=unauthorized).status_code==404
    with gateway.store.connect() as db:
        row=db.execute('SELECT * FROM assets WHERE id=?',(finished[0],)).fetchone()
    download=client.get(f'/api/projects/{client.pid}/assets/'+finished[0]+'/download')
    assert download.status_code==200
    pair={'name':'Study pairs','pairs':[{'sample_id':'sample_A','read1':finished[0],'read2':finished[1]}]}
    bad={'name':'bad','pairs':[{'sample_id':'sample_A','read1':finished[0],'read2':finished[0]}]}
    assert client.post(client.base+'/pairs',json=bad).status_code==422
    result=client.post(client.base+'/pairs',json=pair);assert result.status_code==201,result.text
    assert gateway.store.ids(client.pid,'dataset')==['a'*32]
    assert client.post(client.base+'/pairs',json=pair).status_code==409
    assert len(client.get(client.base).json()['pairs'])==1


def test_changed_staged_chunk_is_rejected_and_can_be_replaced(client,tmp_path):
    data=b'0123456789abcdef';row,body=create(client,data=data)
    path=client.base+'/'+row['id']
    for i in range(2):assert client.put(path+'/chunks/'+str(i),content=data[i*8:(i+1)*8]).status_code==200
    staged=next((tmp_path/'runtime/upload-parts').rglob('0.part'));staged.write_bytes(b'tampered')
    assert client.post(path+'/complete',json={}).status_code==409
    assert client.get(path).json()['status']=='open'
    assert client.put(path+'/chunks/0',content=data[:8]).status_code==200
    assert client.post(path+'/complete',json={}).json()['status']=='completed'


def test_interrupted_finalization_becomes_resumable_after_restart(client):
    data=b'0123456789abcdef';row,body=create(client,data=data)
    path=client.base+'/'+row['id']
    for i in range(2):client.put(path+'/chunks/'+str(i),content=data[i*8:(i+1)*8])
    with gateway.store.connect() as db:db.execute("UPDATE project_uploads SET status='finalizing' WHERE id=?",(row['id'],))
    with TestClient(gateway.app) as reopened:
        result=reopened.post('/api/auth/login',json={'username':'uploader','password':'developer'}).json()
        reopened.headers['X-BioFlow-CSRF']=result['user']['csrf']
        assert reopened.get(path).json()['status']=='paused'
        assert reopened.get(path).json()['received_bytes']==16
        assert reopened.post(path+'/resume',json={}).status_code==200
        assert reopened.post(path+'/complete',json={}).json()['status']=='completed'


def test_ai_analysis_uses_confirmed_pairs_even_for_nonstandard_filenames(client):
    import asyncio
    dataset='b'*32
    gateway.store.claim('dataset',dataset,client.uid)
    with gateway.store.connect() as db:db.execute('INSERT INTO upload_pairs(project_id,user_id,sample_id,read1,read2,dataset_id) VALUES(?,?,?,?,?,?)',
        (client.pid,client.uid,'manual_sample','1'*32,'2'*32,dataset))
    async def call(service,path,method='GET',payload=None):
        assert path=='/api/datasets/'+dataset+'/reuse' and method=='POST'
        return {'dataset_id':dataset,'manifest_path':'/owned/manual.csv','sample_count':1}
    service=assets.AssetService(lambda:gateway.store,call,gateway.SERVICES)
    assert asyncio.run(service.manifest(client.pid,{'id':client.uid}))['manifest_path']=='/owned/manual.csv'
