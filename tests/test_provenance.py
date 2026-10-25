import hashlib,json
import pytest
from fastapi.testclient import TestClient
from backend.bioflow_platform import app as gateway,assets,workspace
from backend.bioflow_platform.store import Store
from backend.bioflow_platform.provenance import graph

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(gateway,'store',Store(tmp_path/'portal.sqlite3'));monkeypatch.setattr(assets,'ROOT',tmp_path);monkeypatch.setattr(workspace,'ROOT',tmp_path)
    c=TestClient(gateway.app);r=c.post('/api/auth/register',json={'username':'lineage','password':'developer'}).json();c.headers['X-BioFlow-CSRF']=r['user']['csrf'];c.uid=r['user']['id'];c.pid=c.post('/api/projects',json={'name':'Study'}).json()['id'];c.base='/api/projects/'+c.pid
    return c

def record(c,key,kind='data',metadata=None):
    with gateway.store.connect() as db:db.execute('INSERT INTO assets(id,project_id,user_id,name,kind,path,size_bytes,sha256,status,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)',(key,c.pid,c.uid,key+'.csv',kind,key,10,'test','ready',json.dumps(metadata or {})))

def task(c,tid):
    gateway.store.claim('task',tid,c.uid);gateway.store.link(c.pid,'task',tid);assets.write_snapshot(tid,{'id':tid,'name':'Task '+tid,'status':'completed','parameters':{'threads':4}},[])

def test_group_filter_counts_shared_asset_and_unclassified(client):
    c=client;task(c,'a');task(c,'b');record(c,'shared');record(c,'unused')
    assert c.put(c.base+'/asset-task-links?asset_id=shared',json={'task_ids':['a','b']}).status_code==200
    for tid in ['a','b']:
        result=c.get(c.base+'/assets?kind=data&task_id='+tid).json();assert [r['id'] for r in result['items']]==['shared'] and result['counts']['data']==1
    assert [r['id'] for r in c.get(c.base+'/assets?task_id=unclassified').json()['items']]==['unused']
    assert c.put(c.base+'/asset-task-links?asset_id=unused',json={'task_ids':['not-owned']}).status_code==422
    other=c.post('/api/projects',json={'name':'Other'}).json()['id']
    assert c.get('/api/projects/'+other+'/asset-links?asset_id=shared').status_code==404
    assert c.put('/api/projects/'+other+'/asset-task-links?asset_id=shared',json={'task_ids':[]}).status_code==404

def test_manual_lineage_is_bidirectional_rejects_cycle_and_checks_citations(client):
    c=client;task(c,'a');record(c,'input');record(c,'result','results',{'task_id':'a'});record(c,'paper','literature');record(c,'draft','drafts')
    assert c.post(c.base+'/asset-links?asset_id=result',json={'source_id':'input'}).status_code==200
    detail=c.get(c.base+'/asset-links?asset_id=input').json();assert detail['relations'][0]['asset']['id']=='result' and detail['tasks'][0]['id']=='a'
    assert c.post(c.base+'/asset-links?asset_id=input',json={'source_id':'result'}).status_code==422
    assert c.post(c.base+'/asset-links?asset_id=result',json={'source_id':'paper','relation':'cites'}).status_code==422
    assert c.post(c.base+'/asset-links?asset_id=draft',json={'source_id':'paper','relation':'cites'}).status_code==200
    assert c.get(c.base+'/asset-links?asset_id=paper').json()['relations'][0]['asset']['id']=='draft'
    assert c.request('DELETE',c.base+'/asset-links?asset_id=draft',json={'source_id':'paper','relation':'cites'}).status_code==200
    assert c.get(c.base+'/asset-links?asset_id=paper').json()['relations']==[]

def test_recorded_figure_source_and_doi_reference_are_not_guessed(client):
    c=client;task(c,'a');task(c,'b')
    rows=[{'id':'result','kind':'results','task_id':'a','artifact_id':'artifact'}, {'id':'fig','kind':'charts','source_id':'artifact:artifact'}, {'id':'paper','kind':'literature','metadata':{'doi':'10.1000/abc'}}, {'id':'draft','kind':'drafts','task_id':'b','references':[{'title':'Citation','doi':'https://doi.org/10.1000/ABC'}]}, {'id':'unrelated','kind':'data','name':'result'}]
    tasks,index,members,edges,_=graph(gateway.store,c.pid,c.uid,rows)
    assert members['fig']=={'a'} and members['paper']=={'b'} and members['unrelated']==set()
    assert any(e['asset_id']=='fig' and e['source_id']=='result' for e in edges)
    assert any(e['asset_id']=='draft' and e['source_id']=='paper' and e['relation']=='cites' for e in edges)

def test_shared_input_does_not_move_known_result_to_another_task(client):
    c=client;task(c,'a');task(c,'b');record(c,'input');record(c,'result','results',{'task_id':'a'})
    c.put(c.base+'/asset-task-links?asset_id=input',json={'task_ids':['a','b']})
    c.post(c.base+'/asset-links?asset_id=result',json={'source_id':'input'})
    assert c.get(c.base+'/asset-links?asset_id=result').json()['tasks'][0]['id']=='a'
    assert len(c.get(c.base+'/asset-links?asset_id=result').json()['tasks'])==1
