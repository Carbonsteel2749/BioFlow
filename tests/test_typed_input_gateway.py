import asyncio
import hashlib
import importlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'BioLLM_RawDataAnalysis'))


def engine(tmp_path):
    from backend.app.services.input_datasets import InputDatasetService
    from backend.app.routes.input_datasets import create_input_dataset_router
    service = InputDatasetService(SimpleNamespace(input_root=tmp_path/'incoming',state_root=tmp_path/'state'))
    app = FastAPI(); app.include_router(create_input_dataset_router(service))
    return service, TestClient(app)


def test_engine_strict_and_immutable(tmp_path):
    service, client = engine(tmp_path)
    folder=service.root/'one';folder.mkdir(parents=True);(folder/'c.fa').write_text('>c\nACGT\n')
    body={'dataset_id':'one','spec':{'kind':'contigs','cohort_id':'c','fasta':'c.fa'}}
    assert client.post('/api/input-datasets/register',json=body).status_code==201
    assert client.get('/api/input-datasets/one').json()['contract']['trust']=='external_unverified'
    body['spec']['cohort_id']='changed'
    assert client.post('/api/input-datasets/register',json=body).status_code==409
    body['spec']['verified']=True
    assert client.post('/api/input-datasets/register',json=body).status_code==422
    assert client.get('/api/input-datasets/missing').status_code==404
    assert client.get('/api/input-datasets/contracts').status_code==200


@pytest.fixture
def gateway(tmp_path,monkeypatch):
    from bioflow_platform import assets
    from bioflow_platform.store import Store
    from bioflow_platform.input_datasets import ImportGateway, install
    service,engine_client=engine(tmp_path)
    monkeypatch.setattr(assets,'ROOT',tmp_path)
    store=Store(tmp_path/'state.db');project=store.create('p','','alice');other=store.create('p2','','alice')
    def remote(method,path,payload=None):
        response=engine_client.request(method,path,json=payload)
        if response.is_error:raise HTTPException(response.status_code,'Validation failed')
        return response.json()
    gateway=ImportGateway(lambda:store,service.root.parent,remote)
    app=FastAPI();router=APIRouter(prefix='/api/projects/{pid}')
    @app.middleware('http')
    async def auth(request,call_next):
        request.state.user={'id':request.headers.get('x-user','alice'),'role':'user'}
        return await call_next(request)
    install(router,lambda:store,lambda p,r:None,{},gateway=gateway)
    app.include_router(router)
    folder=tmp_path/'runtime/assets/alice'/project['id'];folder.mkdir(parents=True)
    data=b'>c\nACGT\n';path=folder/'a.fa';path.write_bytes(data)
    with store.connect() as db:
        db.execute('INSERT INTO assets(id,project_id,user_id,name,kind,path,size_bytes,sha256,status) VALUES(?,?,?,?,?,?,?,?,?)',('a',project['id'],'alice','c.fa','data',f"alice/{project['id']}/a.fa",len(data),hashlib.sha256(data).hexdigest(),'ready'))
    return gateway,store,TestClient(app),'/api/projects/'+project['id']+'/input-datasets',path,other['id']


def payload(key='once'):
    return {'idempotency_key':key,'spec':{'kind':'contigs','cohort_id':'cohort','fasta':'asset:a'}}


def finish(client,base,jid):
    for _ in range(200):
        row=client.get(base+'/imports/'+jid).json()
        if row['status'] in ('ready','failed'):return row
        time.sleep(.01)
    pytest.fail('job did not complete')


def test_import_real_files_duplicate_and_ownership(gateway):
    g,store,c,base,path,other=gateway
    r=c.post(base,json=payload());assert r.status_code==202,r.text
    jid=r.json()['id'];row=finish(c,base,jid);assert row['status']=='ready',row
    record=c.get(base+'/'+jid).json();assert record['contract']['sequence_count']==1
    assert record['spec']['fasta'].startswith('files/')
    assert str(path) not in str(record)
    assert c.post(base,json=payload()).json()['id']==jid
    changed=payload();changed['spec']['cohort_id']='other'
    assert c.post(base,json=changed).status_code==409
    assert c.get(base+'/'+jid,headers={'x-user':'bob'}).status_code==404
    assert c.get(base+'/imports/'+jid,headers={'x-user':'bob'}).status_code==404
    assert c.get(base.replace(base.split('/')[3],other)+'/'+jid).status_code==404
    assert c.post(base.replace(base.split('/')[3],other),json=payload()).status_code==404
    assert len(c.get(base).json()['items'])==1


@pytest.mark.parametrize('change',['hash','size','path','symlink'])
def test_unsafe_sources_fail(gateway,change):
    g,store,c,base,path,other=gateway
    if change=='hash':path.write_text('>c\nTTTT\n')
    if change=='size':g.max_bytes=2
    if change=='path':
        with store.connect() as db:db.execute("UPDATE assets SET path='../outside.fa'")
    if change=='symlink':
        target=path.with_name('target.fa');target.write_bytes(path.read_bytes());path.unlink()
        try:path.symlink_to(target)
        except OSError:pytest.skip('symlink creation not permitted')
    r=c.post(base,json=payload())
    if r.status_code==202:assert finish(c,base,r.json()['id'])['status']=='failed'
    else:assert r.status_code in (404,409,422)


def test_recovery_never_marks_unfinished_ready(gateway):
    g,store,c,base,path,other=gateway
    with store.connect() as db:
        db.execute("INSERT INTO input_dataset_imports(id,project_id,user_id,idempotency_key,request_json,status) VALUES('lost',?,'alice','lost','{}','validating')",(base.split('/')[3],))
    g.recover()
    assert c.get(base+'/imports/lost').json()['status']=='failed'


@pytest.mark.parametrize('path,method',[('api/input-datasets','GET'),('api/input-datasets/register','POST'),('api/input-datasets/foreign','GET')])
def test_proxy_protects_even_developer(tmp_path,path,method):
    from bioflow_platform.access import authorize
    from bioflow_platform.store import Store
    request=SimpleNamespace(state=SimpleNamespace(user={'id':'alice','role':'developer'}),method=method)
    with pytest.raises(HTTPException):asyncio.run(authorize(Store(tmp_path/'s.db'),'analysis',path,request,{}))


@pytest.mark.parametrize('extra',[{'confirmed':False},{'confirmed':'true'},{'confirmed':True,'trust':'platform_verified'}])
def test_external_processing_requires_explicit_confirmation(gateway,extra):
    g,store,c,base,path,other=gateway
    body={'idempotency_key':'reads','spec':{'kind':'reads','processing_status':'clean','processing_note':'external fastp',
          'samples':[{'sample_id':'s','read1':'asset:a','read2':'asset:a'}],**extra}}
    assert c.post(base,json=body).status_code==422


def test_read_pair_import_remains_unverified(gateway):
    g,store,c,base,path,other=gateway;pid=base.split('/')[3]
    for aid,mate in [('r1',1),('r2',2)]:
        f=path.parent/(aid+'.fq');content=f'@x/{mate}\nACGT\n+\nIIII\n'.encode();f.write_bytes(content)
        with store.connect() as db:db.execute('INSERT INTO assets(id,project_id,user_id,name,kind,path,size_bytes,sha256,status) VALUES(?,?,?,?,?,?,?,?,?)',(aid,pid,'alice',aid+'.fq','data',f'alice/{pid}/{aid}.fq',len(content),hashlib.sha256(content).hexdigest(),'ready'))
    body={'idempotency_key':'reads','spec':{'kind':'reads','processing_status':'host_removed','confirmed':True,'processing_note':'external tool',
          'samples':[{'sample_id':'s','read1':'asset:r1','read2':'asset:r2'}]}}
    r=c.post(base,json=body);assert r.status_code==202
    jid=r.json()['id'];assert finish(c,base,jid)['status']=='ready'
    assert c.get(base+'/'+jid).json()['contract']['trust']=='user_declared_unverified'


def test_registered_artifact_reuse_checks_task_owner_and_success(gateway,monkeypatch):
    import json
    from bioflow_platform import assets
    g,store,c,base,path,other=gateway;pid=base.split('/')[3]
    root=path.parents[4]/'analysis';monkeypatch.setenv('BIOFLOW_ANALYSIS_ROOT',str(root))
    output=root/'runtime/outputs/task1';output.mkdir(parents=True);f=output/'c.fa';f.write_bytes(path.read_bytes())
    snap={'task':{'status':'completed'},'artifacts':[{'artifact_id':'out','task_id':'task1','artifact_type':'assembly.contigs','source_file':'c.fa','status':'active','producer':'dynamic.megahit','node_id':'assembly','file_name':'c.fa','sha256':hashlib.sha256(f.read_bytes()).hexdigest(),'size_bytes':f.stat().st_size}]}
    target=assets.snapshot_path('task1');target.parent.mkdir(parents=True);target.write_text(json.dumps(snap))
    original=g.remote
    def authoritative(method,path,payload=None):
        if path=='/api/tasks/task1':return snap['task']
        if path=='/api/tasks/task1/artifacts?metadata_only=true':return snap['artifacts']
        if path=='/api/artifacts/out':return snap['artifacts'][0]
        return original(method,path,payload)
    g.remote=authoritative
    store.claim('task','task1','alice');store.link(pid,'task','task1')
    assert 'artifact:out' in [a['id'] for a in c.get(base+'/candidates').json()['items']]
    body=payload('artifact');body['spec']['fasta']='artifact:out'
    r=c.post(base,json=body);assert r.status_code==202;assert finish(c,base,r.json()['id'])['status']=='ready'
    snap['task']['status']='failed';target.write_text(json.dumps(snap))
    assert 'artifact:out' not in [a['id'] for a in c.get(base+'/candidates').json()['items']]
    body['idempotency_key']='failed';assert c.post(base,json=body).status_code==404


def test_detail_is_import_metadata_but_execution_still_rechecks(gateway,monkeypatch):
    from backend.app.services.input_datasets import InputDatasetService
    from backend.app.workflows.input_contracts import InputContractError
    g,store,c,base,path,other=gateway
    jid=c.post(base,json=payload()).json()['id'];assert finish(c,base,jid)['status']=='ready'
    record=c.get(base+'/'+jid).json();staged=g.input_root/'typed-datasets'/jid/record['spec']['fasta']
    staged.write_text('>c\nTTTT\n')
    def no_remote(*args,**kwargs):raise AssertionError('interactive detail must not call engine')
    monkeypatch.setattr(g,'remote',no_remote)
    response=c.get(base+'/'+jid)
    assert response.status_code==200
    assert response.json()['file_integrity']['state']=='not_rechecked'
    service=InputDatasetService(SimpleNamespace(input_root=g.input_root,state_root=g.input_root.parent/'state'))
    with pytest.raises(InputContractError,match='SHA'):service.snapshot({'input':jid},'run1')


def test_import_process_lock_excludes_other_gateway(gateway):
    from bioflow_platform.input_datasets import import_lock
    g,*_=gateway
    with import_lock(g.input_root):
        with pytest.raises(BlockingIOError):
            with import_lock(g.input_root,blocking=False):pass


def test_private_get_never_hashes_data_files(tmp_path,monkeypatch):
    from backend.app.services import input_datasets as module
    service,c=engine(tmp_path)
    folder=service.root/'one';folder.mkdir(parents=True);(folder/'c.fa').write_text('>c\nACGT\n')
    service.register('one',{'kind':'contigs','cohort_id':'c','fasta':'c.fa'})
    def forbidden(path):raise AssertionError('full data hash on interactive GET')
    monkeypatch.setattr(module,'_file_sha',forbidden)
    response=c.get('/api/input-datasets/one')
    assert response.status_code==200
    assert response.json()['file_integrity']['state']=='not_rechecked'


@pytest.mark.parametrize('kind',[[],{},1,None])
def test_invalid_kind_is_422_in_gateway_and_validation_error_in_core(gateway,kind):
    from backend.app.workflows.input_contracts import validate_input_contract,InputContractError
    g,store,c,base,path,other=gateway
    assert c.post(base,json={'idempotency_key':'invalid','spec':{'kind':kind}}).status_code==422
    with pytest.raises(InputContractError):validate_input_contract({'kind':kind},path.parent)


@pytest.mark.parametrize('code_name',['EIO','ENOLCK'])
def test_permanent_lock_errors_do_not_retry_and_job_fails(gateway,monkeypatch,code_name):
    import errno,os
    from bioflow_platform import input_datasets as module
    g,store,c,base,path,other=gateway
    def broken(*args):raise OSError(getattr(errno,code_name),'controlled permanent error')
    if os.name=='nt':
        import msvcrt
        monkeypatch.setattr(msvcrt,'locking',broken)
    else:
        import fcntl
        monkeypatch.setattr(fcntl,'flock',broken)
    def no_sleep(*args):raise AssertionError('permanent lock error retried')
    monkeypatch.setattr(module.time,'sleep',no_sleep)
    with pytest.raises(OSError) as exc:
        with module.import_lock(g.input_root):pass
    assert exc.value.errno==getattr(errno,code_name)
    pid=base.split('/')[3]
    with store.connect() as db:db.execute("INSERT INTO input_dataset_imports(id,project_id,user_id,idempotency_key,request_json,status) VALUES('lockfail',?,'alice','lockfail','{}','queued')",(pid,))
    g.run(pid,'alice','lockfail')
    assert g.status(pid,'alice','lockfail')['status']=='failed'


def test_lock_open_failure_persists_failed_job(gateway,monkeypatch):
    g,store,c,base,path,other=gateway;pid=base.split('/')[3]
    original=Path.open
    def broken(self,*args,**kwargs):
        if self.name=='.typed-import.lock':raise PermissionError('controlled lock open failure')
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Path,'open',broken)
    with store.connect() as db:db.execute("INSERT INTO input_dataset_imports(id,project_id,user_id,idempotency_key,request_json,status) VALUES('openfail',?,'alice','openfail','{}','queued')",(pid,))
    g.run(pid,'alice','openfail')
    assert g.status(pid,'alice','openfail')['status']=='failed'


@pytest.mark.parametrize('stage',['before','during','unavailable'])
def test_import_space_guards_keep_originals(gateway,monkeypatch,stage):
    from bioflow_platform import input_datasets as module
    g,store,c,base,path,other=gateway
    original=path.read_bytes();observations=[]
    def disk(root):
        observations.append(root)
        if stage=='unavailable':raise OSError('private /secret/location')
        return SimpleNamespace(free=(g.reserve_bytes+100 if stage=='during' and len(observations)==1 else 0))
    monkeypatch.setattr(module.shutil,'disk_usage',disk)
    r=c.post(base,json=payload('space'));assert r.status_code==202
    row=finish(c,base,r.json()['id']);assert row['status']=='failed'
    assert '存储空间' in row['error'] and '/secret' not in row['error']
    assert path.read_bytes()==original
    if stage in ('before','unavailable'):assert not (g.input_root/'typed-datasets'/row['id']).exists()


def test_browser_origin_forgery_is_rejected(gateway):
    g,store,c,base,path,other=gateway
    p=payload();p['origin']={'authority':'owned_import_gateway'}
    assert c.post(base,json=p).status_code==422
    p=payload();p['spec']['origin']={'authority':'owned_import_gateway'}
    assert c.post(base,json=p).status_code==422


def test_zero_byte_source_is_not_a_valid_space_estimate(gateway):
    g,store,c,base,path,other=gateway
    path.write_bytes(b'')
    with store.connect() as db:db.execute('UPDATE assets SET size_bytes=0,sha256=?',(hashlib.sha256(b'').hexdigest(),))
    assert c.post(base,json=payload()).status_code==409
    assert not c.get(base+'/candidates').json()['items']


def test_specific_private_validation_message_without_paths(tmp_path):
    service,c=engine(tmp_path);folder=service.root/'bad';folder.mkdir(parents=True)
    (folder/'a.fa').write_bytes(b'>protein\nEFILPQ\n')
    r=c.post('/api/input-datasets/register',json={'dataset_id':'bad','spec':{'kind':'contigs','cohort_id':'c','fasta':'a.fa'}})
    assert r.status_code==422
    assert r.json()['detail']['code']=='input_contract_invalid'
    assert '核酸' in r.json()['detail']['message']
    assert str(tmp_path) not in r.text


def test_production_http_adapter_preserves_controlled_worker_diagnostic(gateway,monkeypatch):
    from bioflow_platform import input_datasets as module,assets
    g,store,c,base,path,other=gateway
    service,private=engine(g.input_root.parent)
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def request(self,method,url,json=None):return private.request(method,url.replace('http://engine',''),json=json)
    monkeypatch.setattr(module.httpx,'Client',Client)
    monkeypatch.setattr(assets,'analysis_root',lambda:g.input_root.parent)
    production=module.install(APIRouter(),lambda:store,lambda *args:None,{'analysis':'http://engine'})
    g.remote=production.remote
    data=b'>protein\nEFILPQ\n';path.write_bytes(data)
    with store.connect() as db:db.execute('UPDATE assets SET size_bytes=?,sha256=? WHERE id=?',(len(data),hashlib.sha256(data).hexdigest(),'a'))
    r=c.post(base,json=payload('invalid-fasta'));row=finish(c,base,r.json()['id'])
    assert row['status']=='failed'
    assert '核酸' in row['error']
    assert str(path) not in row['error']
