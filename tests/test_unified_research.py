import hashlib
import json
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.bioflow_platform import app as gateway,assets,research_store as rs,research_queue
from backend.bioflow_platform.store import Store

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'runtime/research-python/bin/python'

@pytest.fixture
def client(tmp_path,monkeypatch):
    if not ENGINE.exists():pytest.skip('install deploy/requirements-research.txt in dedicated compute environment')
    monkeypatch.setattr(gateway,'store',Store(tmp_path/'portal.sqlite3'))
    monkeypatch.setattr(assets,'ROOT',tmp_path)
    monkeypatch.setenv('BIOFLOW_RESEARCH_PYTHON',str(ENGINE));monkeypatch.setenv('BIOFLOW_RESEARCH_AUTOSTART','0')
    c=TestClient(gateway.app)
    registered=c.post('/api/auth/register',json={'username':'researcher','password':'Test-password-2026!'})
    c.headers['X-BioFlow-CSRF']=registered.json()['user']['csrf'];c.uid=registered.json()['user']['id']
    c.pid=c.post('/api/projects',json={'name':'Table research'}).json()['id'];c.base='/api/projects/'+c.pid+'/research'
    return c

MATRIX=b'feature,S1,S2,S3,S4\nA,1,2,3,4\nB,4,3,2,1\nC,2,2,2,2\n'

def upload(c,name='matrix.tsv',content=MATRIX,role='taxonomy',**options):
    response=c.post(c.base+'/data/import',files={'file':(name,content)},data={'options':json.dumps({'role':role,'unit':'counts' if role=='taxonomy' else 'auto',**options})})
    assert response.status_code==201,response.text
    return response.json()

def plan(c,ids,steps=None,study=None):
    steps=steps or [{'id':'s1','skill':'alpha_diversity','parameters':{},'depends_on':[]}]
    response=c.post(c.base+'/plans',json={'objective':'计算各样本 Shannon 多样性','dataset_ids':ids,'mode':'manual','steps':steps,'study':study or {}})
    assert response.status_code==201,response.text
    return response.json()

def confirm(c,p):
    r=c.post(c.base+'/plans/'+p['id']+'/confirm',json={'fingerprint':p['fingerprint']});assert r.status_code==202,r.text
    return r.json()

def test_real_import_plan_run_and_registered_results(client):
    d=upload(client);assert d['schema']['rows']==3 and len(d['samples'])==4
    catalog=client.get(client.base+'/tools').json();assert 'alpha_diversity' in {t['id'] for t in catalog['tools']}
    assert 'custom_analysis' not in {t['id'] for t in catalog['tools']}
    p=plan(client,[d['id']]);assert p['status']=='ready',p
    r=confirm(client,p);assert confirm(client,p)['id']==r['id']
    assert research_queue.run_one(gateway.store)
    done=client.get(client.base+'/runs/'+r['id']).json();assert done['status']=='completed',done
    result=done['step_results'][0];assert 'raw_engine_result' not in result
    table=next(a for a in result['artifacts'] if a['artifact_type']=='table')
    content=client.get(client.base+'/artifacts/'+table['id']);assert content.status_code==200
    assert b'S1' in content.content and b'shannon' in content.content.lower()
    assert hashlib.sha256(content.content).hexdigest()==table['sha256']
    events=client.get(client.base+'/runs/'+r['id']+'/events').json()['items']
    assert {'queued','started','step_started','step_completed','completed'}<={e['event'] for e in events}
    sources=client.get('/api/projects/'+client.pid+'/asset-tasks').json();assert any(t['id']==r['id'] and t['count']>=3 for t in sources['items']),sources
    from backend.bioflow_platform.library_management import automatic_folders
    library=client.get('/api/projects/'+client.pid+'/assets').json()
    assert table['id'] in {a['id'] for a in library['items']},library
    with gateway.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM assets WHERE id=?',(table['id'],)).fetchone()[0]==1

def test_existing_result_table_plot(client):
    d=upload(client,'effects.csv',b'predictor,beta,ci_low,ci_high\nA,2,1,3\nB,-1,-2,-0.5\n',role='results')
    p=plan(client,[d['id']],[{'id':'s1','skill':'existing_effect_plot','parameters':{'dataset_id':d['id'],'result_effect':'beta'},'depends_on':[]}])
    r=confirm(client,p);research_queue.run_one(gateway.store)
    done=client.get(client.base+'/runs/'+r['id']).json();assert done['status']=='completed',done
    artifacts=done['step_results'][0]['artifacts'];assert {'png','svg','table'}<={a['artifact_type'] for a in artifacts}
    assert '未重新计算统计检验' in done['step_results'][0]['summary']

def test_import_idempotence_versions_and_snapshot_integrity(client):
    d=upload(client)
    same=client.post(client.base+'/data/register',json={'asset_id':d['source_asset_id'],'options':d['options']})
    assert same.status_code==201 and same.json()['id']==d['id']
    p=plan(client,[d['id']])
    bad=client.post(client.base+'/plans/'+p['id']+'/confirm',json={'fingerprint':'a'*64});assert bad.status_code==409
    directory=rs.verify_dataset(rs.get(gateway.store,client.pid,client.uid,d['id']))
    (directory/'matrix.json.gz').write_bytes(b'changed')
    bad=client.post(client.base+'/plans/'+p['id']+'/confirm',json={'fingerprint':p['fingerprint']});assert bad.status_code==422

def test_foreign_inputs_and_artifacts_are_not_accessible(client):
    d=upload(client)
    other=TestClient(gateway.app);user=other.post('/api/auth/register',json={'username':'outsider','password':'Test-password-2026!'})
    other.headers['X-BioFlow-CSRF']=user.json()['user']['csrf']
    assert other.get(client.base+'/data').status_code==404
    pid=other.post('/api/projects',json={'name':'Other'}).json()['id'];base='/api/projects/'+pid+'/research'
    response=other.post(base+'/data/register',json={'asset_id':d['source_asset_id'],'options':d['options']});assert response.status_code==404
    response=other.post(base+'/plans',json={'objective':'test','dataset_ids':[d['id']],'steps':[{'id':'s1','skill':'alpha_diversity'}]});assert response.status_code==404

def test_cancelled_queue_can_resume_and_reuse_completed_steps(client):
    d=upload(client);p=plan(client,[d['id']]);r=confirm(client,p)
    assert client.post(client.base+'/runs/'+r['id']+'/cancel').json()['status']=='cancelled'
    assert not research_queue.run_one(gateway.store)
    assert client.post(client.base+'/runs/'+r['id']+'/resume').status_code==200
    assert research_queue.run_one(gateway.store)
    current=rs.get(gateway.store,client.pid,client.uid,r['id']);assert current['status']=='completed'
    rs.update(gateway.store,current,'interrupted')
    assert client.post(client.base+'/runs/'+r['id']+'/resume').status_code==200
    assert research_queue.run_one(gateway.store)
    events=client.get(client.base+'/runs/'+r['id']+'/events').json()['items'];assert any(e['event']=='step_reused' for e in events)

def test_unknown_tool_and_raw_sequence_upload_rejected(client):
    assert client.post(client.base+'/data/import',files={'file':('reads.fastq',b'@x\nAC\n+\nII')},data={'options':'{"role":"taxonomy"}'}).status_code==422
    d=upload(client)
    response=client.post(client.base+'/plans',json={'objective':'unsupported','dataset_ids':[d['id']],'steps':[{'id':'s1','skill':'custom_analysis'}]})
    assert response.status_code==422

def test_original_bioflow_pca_uses_unified_inputs_and_outputs(client):
    d=upload(client)
    p=plan(client,[d['id']],[{'id':'s1','skill':'bioflow_pca','parameters':{'dataset_id':d['id'],'plot_options':{'show_x_label':False,'title':'Unified PCA'}},'depends_on':[]}])
    assert p['steps'][0]['parameters']['plot_options']['show_x_label'] is False
    r=confirm(client,p);research_queue.run_one(gateway.store)
    done=client.get(client.base+'/runs/'+r['id']).json();assert done['status']=='completed',json.dumps(done,ensure_ascii=False)
    result=done['step_results'][0];assert {'png','svg','pdf','table'}<={a['artifact_type'] for a in result['artifacts']}
    svg=next(a for a in result['artifacts'] if a['artifact_type']=='svg')
    assert b'Unified PCA' in client.get(client.base+'/artifacts/'+svg['id']).content

def test_real_named_result_binding_and_failure_blocks_dependents(client):
    d=upload(client)
    steps=[{'id':'s1','skill':'alpha_diversity','parameters':{},'depends_on':[]},
           {'id':'s2','skill':'bioflow_heatmap','parameters':{},'depends_on':['s1'],'inputs':{'matrix':{'step_id':'s1','output':'alpha_diversity'}}}]
    p=plan(client,[d['id']],steps);r=confirm(client,p);research_queue.run_one(gateway.store)
    done=client.get(client.base+'/runs/'+r['id']).json();assert done['status']=='completed',json.dumps(done,ensure_ascii=False)
    assert len(done['step_results'])==2

def test_running_cancel_terminates_owned_step_and_can_resume(client):
    import threading,time
    d=upload(client);r=confirm(client,plan(client,[d['id']]))
    thread=threading.Thread(target=research_queue.run_one,args=(gateway.store,));thread.start()
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        events=client.get(client.base+'/runs/'+r['id']+'/events').json()['items']
        if any(e['event']=='step_started' for e in events):break
        time.sleep(.1)
    else:pytest.fail('worker did not begin the step')
    response=client.post(client.base+'/runs/'+r['id']+'/cancel');assert response.status_code==200
    thread.join(15);assert not thread.is_alive()
    done=client.get(client.base+'/runs/'+r['id']).json();assert done['status']=='cancelled',done
    assert client.post(client.base+'/runs/'+r['id']+'/resume').status_code==200
    assert research_queue.run_one(gateway.store)
    assert client.get(client.base+'/runs/'+r['id']).json()['status']=='completed'
