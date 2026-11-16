import hashlib
import pytest
from fastapi import HTTPException
from test_typed_input_gateway import gateway,finish


@pytest.fixture
def private_metadata_catalog(tmp_path,monkeypatch):
    """Real private router/service/repository; forbid publication and content IO."""
    from pathlib import Path
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.artifacts import ArtifactRepository
    from backend.app.services import artifacts,stage_figures
    from backend.app.routes import artifacts as routes
    root=tmp_path/'private/runtime/outputs';source=root/'run1/nested/c.fa'
    source.parent.mkdir(parents=True);source.write_bytes(b'>c\nACGT\n')
    repository=ArtifactRepository(tmp_path/'private/artifacts.db');repository.initialize()
    service=artifacts.ArtifactService(repository,root)
    registered=service.register(task_id='run1',producer='assembly',artifact_type='assembly.contigs',path=source,media_type='text/plain')
    app=FastAPI();app.include_router(routes.create_artifact_router(service,lambda tid:tid=='run1'))
    @app.get('/api/workflows/runs/{tid}')
    def run(tid:str):return {'task_id':tid,'status':'succeeded'}
    client=TestClient(app);invocations=[]
    def forbidden(*args,**kwargs):
        invocations.append('publication/read/hash')
        raise AssertionError('interactive listing must not publish, read or hash file content')
    monkeypatch.setattr(routes,'publish_stage_figures',forbidden)
    monkeypatch.setattr(stage_figures,'_publish_companions',forbidden)
    monkeypatch.setattr(stage_figures,'_refresh_figure_context',forbidden)
    monkeypatch.setattr(artifacts,'_sha256',forbidden)
    for name in ('open','read_text','read_bytes','glob'):
        original=getattr(Path,name)
        def guard(path,*args,_original=original,**kwargs):
            if path.is_relative_to(root.parent):return forbidden()
            return _original(path,*args,**kwargs)
        monkeypatch.setattr(Path,name,guard)
    monkeypatch.setenv('BIOFLOW_ANALYSIS_ROOT',str(root.parents[1]))
    return client,registered,invocations


def test_candidates_use_real_private_metadata_only_handler(gateway,private_metadata_catalog):
    g,store,c,base,_,_=gateway;private,record,invocations=private_metadata_catalog
    pid=base.split('/')[3];store.claim('workflow','run1','alice');store.link(pid,'workflow','run1')
    def remote(method,path,payload=None):
        response=private.request(method,path,json=payload)
        response.raise_for_status();return response.json()
    g.remote=remote
    response=c.get(base+'/candidates');assert response.status_code==200,response.text
    assert 'artifact:'+record['artifact_id'] in {item['id'] for item in response.json()['items']}
    assert invocations==[]


def test_private_metadata_mode_preserves_legacy_publication(private_metadata_catalog,monkeypatch):
    from backend.app.routes import artifacts as routes
    private,record,invocations=private_metadata_catalog;published=[]
    monkeypatch.setattr(routes,'publish_stage_figures',lambda service,tid:published.append(tid))
    response=private.get('/api/tasks/run1/artifacts?metadata_only=true')
    assert response.status_code==200 and published==[]
    assert response.json()[0]['artifact_id']==record['artifact_id']
    assert private.get('/api/tasks/run1/artifacts').status_code==200
    assert published==['run1'] and invocations==[]
    assert private.get('/api/tasks/run1/artifacts?metadata_only=false').status_code==200
    assert published==['run1','run1']

@pytest.fixture
def reuse(gateway,monkeypatch):
    g,store,c,base,path,other=gateway;pid=base.split('/')[3]
    root=path.parents[4]/'analysis';monkeypatch.setenv('BIOFLOW_ANALYSIS_ROOT',str(root))
    out=root/'runtime/outputs/run1/nested';out.mkdir(parents=True);f=out/'contigs.fa';f.write_bytes(b'>c\nACGT\n')
    row=dict(artifact_id='out',task_id='run1',node_id='assembly',producer='dynamic.assembly',artifact_type='assembly.contigs',source_file='nested/contigs.fa',file_name='contigs.fa',sha256=hashlib.sha256(f.read_bytes()).hexdigest(),size_bytes=f.stat().st_size,status='active')
    status={'task_id':'run1','status':'succeeded'};calls=[];original=g.remote
    def remote(method,path,payload=None):
        calls.append(path)
        if path=='/api/workflows/runs/run1':return status
        if path=='/api/tasks/run1/artifacts?metadata_only=true':return [row]
        if path=='/api/artifacts/out':return row
        return original(method,path,payload)
    g.remote=remote;store.claim('workflow','run1','alice');store.link(pid,'workflow','run1')
    return gateway,row,status,calls,f

def test_workflow_authoritative_reuse(reuse):
    (g,store,c,base,path,other),row,status,calls,f=reuse
    response=c.get(base+'/candidates');assert response.status_code==200,response.text
    assert 'artifact:out' in [x['id'] for x in response.json()['items']]
    assert calls.count('/api/workflows/runs/run1')==1
    assert '/api/artifacts/out' not in calls
    assert str(f) not in response.text and 'source_file' not in response.text
    body={'idempotency_key':'reuse','spec':dict(kind='contigs',cohort_id='c',fasta='artifact:out')}
    response=c.post(base,json=body);assert response.status_code==202,response.text
    assert finish(c,base,response.json()['id'])['status']=='ready'

@pytest.mark.parametrize('change',['foreign_project','foreign_user','producer_mismatch','failed','inactive','escape','hash'])
def test_reuse_rejects_stale_or_foreign(reuse,change):
    (g,store,c,base,path,other),row,status,calls,f=reuse
    if change=='foreign_project':base=base.replace(base.split('/')[3],other)
    if change=='foreign_user':
        with store.connect() as db:db.execute("UPDATE resources SET user_id='bob' WHERE external_id='run1'")
    if change=='producer_mismatch':row['task_id']='someone_else'
    if change=='failed':status['status']='failed'
    if change=='inactive':row['status']='stale'
    if change=='escape':row['source_file']='../nested/contigs.fa'
    if change=='hash':f.write_bytes(b'>c\nTTTT\n')
    response=c.post(base,json={'idempotency_key':'bad','spec':dict(kind='contigs',cohort_id='c',fasta='artifact:out')})
    if response.status_code==202:assert finish(c,base,response.json()['id'])['status']=='failed'
    else:assert response.status_code in (404,409,422)

def test_unavailable_is_not_empty_success(reuse):
    (g,store,c,base,path,other),row,status,calls,f=reuse
    def unavailable(*args):raise RuntimeError('/secret/private/path')
    g.remote=unavailable
    response=c.get(base+'/candidates')
    assert response.status_code==503 and 'secret' not in response.text

def test_paging_is_usable_without_duplicate_sources(reuse):
    (g,store,c,base,path,other),row,status,calls,f=reuse
    seen=[];params={'limit':1}
    for _ in range(5):
        response=c.get(base+'/candidates',params=params);assert response.status_code==200
        page=response.json();seen.extend(r['id'] for r in page['items'])
        if not page['has_more']:break
        params=page['next']
    else:pytest.fail('pagination did not end')
    assert seen==['asset:a','artifact:out']

def test_catalog_caps_match_private_contract(gateway):
    from bioflow_platform.input_datasets import CATALOG
    from backend.app.workflows.input_contracts import NUMERIC_POLICY,VALIDATOR_CAPS
    assert CATALOG['numeric_policy']==NUMERIC_POLICY
    assert CATALOG['validator_caps']==VALIDATOR_CAPS

def test_symlinked_artifact_rejected(reuse):
    (g,store,c,base,path,other),row,status,calls,f=reuse
    link=f.with_name('link.fa')
    try:link.symlink_to(f)
    except OSError:pytest.skip('symlink creation not permitted')
    row['source_file']='nested/link.fa'
    response=c.post(base,json={'idempotency_key':'link','spec':dict(kind='contigs',cohort_id='c',fasta='artifact:out')})
    assert response.status_code==409

def test_transport_get_is_short_post_remains_long(gateway,monkeypatch):
    from bioflow_platform import input_datasets as module
    from fastapi import APIRouter
    g,store,c,base,path,other=gateway;timeouts=[]
    class Client:
        def __init__(self,**kwargs):timeouts.append(kwargs['timeout'])
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def request(self,*args,**kwargs):
            from types import SimpleNamespace
            return SimpleNamespace(is_error=False,json=lambda:{})
    monkeypatch.setattr(module.httpx,'Client',Client)
    created=module.install(APIRouter(),lambda:store,lambda *args:None,{'analysis':'http://private'})
    created.remote('GET','/api/artifacts/a');created.remote('POST','/api/input-datasets/register',{})
    assert [t.read for t in timeouts]==[10,86400]
    assert all(t.connect==5 for t in timeouts)

def test_reused_rich_result_passes_real_conversion_and_private_service(reuse):
    import csv
    from test_typed_conversion import SPECIES,species
    (g,store,c,base,path,other),row,status,calls,f=reuse
    with f.open('w',newline='') as h:
        writer=csv.DictWriter(h,fieldnames=SPECIES,delimiter='\t');writer.writeheader();writer.writerow(species())
    row.update(file_name='species.tsv',artifact_type='taxonomy.species_abundance',size_bytes=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest())
    response=c.post(base,json={'idempotency_key':'rich','spec':dict(kind='matrix',table='artifact:out',feature_type='species',unit='fraction',normalization='none')})
    assert response.status_code==202,response.text
    jid=response.json()['id'];assert finish(c,base,jid)['status']=='ready'
    record=c.get(base+'/'+jid).json()
    assert record['origin']['sources'][0]['source_task']=='run1'
    assert record['origin']['sources'][0]['sha256']==row['sha256']
    assert 'hints' not in record['origin']['sources'][0]
    assert record['origin']['conversion']['included_rows']==1
    assert record['contract']['trust']=='external_unverified'


def test_registered_refined_mag_is_owned_discoverable_and_reimportable(reuse,tmp_path):
    from backend.app.artifacts import ArtifactRepository
    from backend.app.services.artifacts import ArtifactService
    (g,store,c,base,path,other),row,status,calls,f=reuse
    refined=f.parent/'MAG1.fa';refined.write_text('>c\nACGT\n')
    repository=ArtifactRepository(tmp_path/'mag-index.db');repository.initialize()
    service=ArtifactService(repository,f.parents[2])
    real=service.register(task_id='run1',node_id='refinement',producer='dynamic.bin_refinement',artifact_type='mag.fasta',
        path=refined,media_type='text/plain',sample_scope='cohort',cohort_id='branch_A',metadata={'quality_status':'threshold_filtered_unverified'})
    row.clear();row.update(real)
    original=g.remote
    def remote(method,path,payload=None):
        if path=='/api/artifacts/'+real['artifact_id']:return real
        return original(method,path,payload)
    g.remote=remote
    response=c.get(base+'/candidates');assert response.status_code==200,response.text
    candidate=next(x for x in response.json()['items'] if x['id']=='artifact:'+real['artifact_id'])
    assert candidate['hints']['cohort_id']=='branch_A'
    response=c.post(base,json={'idempotency_key':'mag-reuse','spec':dict(kind='mags',cohort_id='branch_A',mags=[dict(mag_id='MAG1',fasta=candidate['id'])])})
    assert response.status_code==202,response.text
    jid=response.json()['id'];assert finish(c,base,jid)['status']=='ready'
    detail=c.get(base+'/'+jid).json();assert detail['contract']['trust']=='external_unverified'
    assert detail['contract']['cohort_id']=='branch_A'


def test_import_history_exposes_only_scoped_opaque_request_digest(gateway):
    g,store,c,base,path,other=gateway
    response=c.post(base,json={'idempotency_key':'lost-client-key','spec':dict(kind='contigs',cohort_id='cohort',fasta='asset:a')})
    assert response.status_code==202,response.text
    items=c.get(base).json()['items'];assert items[0]['request_id']==hashlib.sha256(b'lost-client-key').hexdigest()
    assert 'lost-client-key' not in c.get(base).text and 'request_json' not in c.get(base).text
