import hashlib
import pytest
from fastapi import HTTPException
from test_typed_input_gateway import gateway,finish

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
        if path=='/api/tasks/run1/artifacts':return [row]
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
