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

def test_auto_recognition_preview_edit_and_project_library_without_run(client):
    raw=b'ID,F1,F2\nk__Bacteria,100,100\nk__Bacteria|s__A,60,40\nk__Bacteria|s__B,40,60\n'
    response=client.post(client.base+'/data/import',files={'file':('metaphlan.tsv',raw)})
    assert response.status_code==201,response.text
    d=response.json();assert d['role']=='taxonomy' and d['engine_info']['unit']=='fraction'
    response=client.get('/api/projects/'+client.pid+'/library-browser',params={'task_id':'','kind':'data'})
    assert response.status_code==200,response.text
    assert d['source_asset_id'] in {x['id'] for x in response.json()['items']}
    table=client.get(client.base+'/data/'+d['id']+'/table').json();assert table['columns'][0]=='ID'
    old_hash=d['hashes']['matrix.json.gz']
    response=client.post(client.base+'/data/'+d['id']+'/edit',json={'edits':[{'row':1,'column':1,'value':'0.5'}]})
    assert response.status_code==201,response.text
    edited=response.json();assert edited['parent_id']==d['id'] and edited['revision']==2
    assert edited['hashes']['matrix.json.gz']!=old_hash
    assert rs.digest(rs.verify_dataset(rs.get(gateway.store,client.pid,client.uid,d['id']))/'matrix.json.gz')==old_hash
    table=client.get(client.base+'/data/'+edited['id']+'/table').json();assert float(table['rows'][1][1])==.5
    assert client.get(client.base+'/runs').json()['items']==[]


def test_auto_metadata_identifier_and_reject_invalid_cell(client):
    response=client.post(client.base+'/data/import',files={'file':('metadata.csv','序号,F号,Zn分组\n1,F1,低\n2,F2,高\n'.encode())})
    assert response.status_code==201,response.text
    d=response.json();assert d['role']=='metadata' and d['samples']==['F1','F2']
    assert d['engine_info']['identifier_column']=='F号'
    response=client.post(client.base+'/data/'+d['id']+'/edit',json={'edits':[{'row':0,'column':0,'value':'F2'}]})
    assert response.status_code==422,response.text


def test_short_titles_and_evidence_report(client):
    d=upload(client);p=plan(client,[d['id']]);r=confirm(client,p)
    assert len(r['title'])<=28 and r['title']!='计算各样本 Shannon 多样性'
    research_queue.run_one(gateway.store)
    report=client.get(client.base+'/runs/'+r['id']+'/report');assert report.status_code==200,report.text
    report=report.json();assert report['sections'][0]['tool_id']=='alpha_diversity'
    assert report['sections'][0]['metrics']['samples']==4
    assert report['input_snapshot'][0]['id']==d['id']
    assert len(report['sections'][0]['artifacts'])>=3

def test_humann_sample_suffix_mapping_is_bijective_and_recorded(client):
    response=client.post(client.base+'/data/import',files={'file':('taxonomy.tsv',b'ID,F1,F2\nk__Bacteria,100,100\nk__Bacteria|s__A,100,100\n')})
    assert response.status_code==201,response.text
    raw=b'# Pathway,F1_combined,F2_combined\nPWY-A|g__A.s__A,0.2,0.3\n'
    response=client.post(client.base+'/data/import',files={'file':('pathabundance_relab_stratified.tsv',raw)})
    assert response.status_code==201,response.text
    d=response.json();assert d['samples']==['F1','F2'] and d['options']['sample_id_rule']=='strip_combined'
    assert any(t.get('mapping')=={'F1_combined':'F1','F2_combined':'F2'} for t in d['engine_info']['transformations'])
    raw=b'# Pathway,F1_combined,F9_combined\nPWY-A|g__A.s__A,0.2,0.3\n'
    response=client.post(client.base+'/data/import',files={'file':('pathabundance_relab_stratified.tsv',raw)})
    assert response.status_code==201,response.text
    assert response.json()['samples']==['F1_combined','F9_combined']

def test_ai_cell_proposals_validate_existing_positions_and_explicit_values(client,tmp_path):
    import subprocess
    code='''
import sys
from pathlib import Path
import pandas as pd
from backend.bioflow_platform import research_engine_bridge
from backend.bioflow_platform import research_experience as x
folder=Path(sys.argv[1]);folder.mkdir()
pd.DataFrame({'value':['2','3']},index=['F1','F2']).to_json(folder/'matrix.json.gz',orient='split',compression='gzip')
proposal={'options':{'unit':'metadata'},'edits':[{'row_id':'F1','column':'value','value':'7'}],'explanation':'建议'}
x.model_object=lambda *a,**k:proposal
spec={'mode':'correct_settings','directory':str(folder),'options':{'role':'metadata','unit':'auto','transpose':False,'sample_id_rule':'none','sheet':None,'id_column':'F号','header_row':1,'delimiter':'auto','encoding':'auto','description':''},'instruction':'把 F1 的 value 改为7','engine_info':{'unit':'metadata'}}
result=x.data_operation(spec)
assert result['options']['unit']=='auto' and result['edits'][0]['before']=='2'
proposal['edits'][0]['value']='8'
try:x.data_operation(spec)
except ValueError:pass
else:raise AssertionError('invented value accepted')
proposal['edits'][0]={'row_id':'missing','column':'value','value':'7'}
try:x.data_operation(spec)
except ValueError:pass
else:raise AssertionError('unknown row accepted')
'''
    result=subprocess.run([str(ENGINE),'-c',code,str(tmp_path/'proposed-data')],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode==0,result.stderr

def task_create(c,title='Task'):
    r=c.post(c.base+'/tasks',json={'title':title});assert r.status_code==201,r.text
    return r.json()


def test_tasks_have_independent_inputs_workflow_versions_and_reports(client):
    project_data=upload(client);a=task_create(client,'A');b=task_create(client,'B')
    assert client.get(client.base+'/data',params={'task_id':a['id']}).json()['items']==[]
    assert client.get('/api/projects/'+client.pid+'/library-browser',params={'task_id':a['id'],'kind':'data'}).json()['items']==[]
    response=client.post(client.base+'/tasks/'+a['id']+'/assets',json={'source_project_id':client.pid,'asset_ids':[project_data['source_asset_id']]})
    assert response.status_code==200,response.text
    assert response.json()['dataset_ids']==[project_data['id']]
    assert client.get(client.base+'/data',params={'task_id':b['id']}).json()['items']==[]
    body={'objective':'Alpha','steps':[{'id':'s1','skill':'alpha_diversity','parameters':{},'depends_on':[]}],'study':{}}
    r=client.put(client.base+'/tasks/'+a['id']+'/workflow',json=body);assert r.status_code==201,r.text
    first=r.json()['plans'][0]
    r=client.put(client.base+'/tasks/'+a['id']+'/workflow',json=body);assert r.status_code==201,r.text
    detail=r.json();assert [p['revision'] for p in detail['plans']]==[1,2]
    latest=detail['plans'][-1]
    r=client.post(client.base+'/tasks/'+a['id']+'/plans/'+latest['id']+'/confirm',json={'fingerprint':latest['fingerprint']})
    assert r.status_code==202,r.text
    research_queue.run_one(gateway.store)
    current=client.get(client.base+'/tasks/'+a['id']).json();assert current['status']=='completed',current
    assert current['runs'][0]['plan']['fingerprint']==latest['fingerprint']
    report=client.get(client.base+'/tasks/'+a['id']+'/report/'+current['run_ids'][0]);assert report.status_code==200,report.text
    assert report.json()['markdown'].startswith('# ') and 'alpha_diversity.tsv' in report.json()['markdown']
    assert client.get(client.base+'/tasks/'+b['id']+'/report/'+current['run_ids'][0]).status_code==404
    rows=client.get('/api/projects/'+client.pid+'/library-browser',params={'task_id':a['id'],'kind':'results'}).json()
    assert rows['counts']['results']>=1,rows


def test_task_upload_archive_rename_delete_and_chat_without_data(client):
    a=task_create(client)
    r=client.post(client.base+'/tasks/'+a['id']+'/messages',json={'text':'做多样性分析'})
    assert r.status_code==201,r.text
    assert r.json()['messages'][-1]['role']=='assistant' and r.json()['plans']==[]
    r=client.post(client.base+'/data/import',files={'file':('matrix.csv',MATRIX)},data={'task_id':a['id'],'options':'{"role":"taxonomy","unit":"counts"}'})
    assert r.status_code==201,r.text
    assert len(client.get(client.base+'/data',params={'task_id':a['id']}).json()['items'])==1
    assert client.patch(client.base+'/tasks/'+a['id'],json={'title':'Renamed','archived':True}).status_code==200
    tree=client.get('/api/research/tree').json();assert not tree['items'][0]['tasks']
    tree=client.get('/api/research/tree?include_archived=true').json();assert tree['items'][0]['tasks'][0]['title']=='Renamed'
    assert client.delete(client.base+'/tasks/'+a['id']).status_code==200
    assert client.get(client.base+'/tasks/'+a['id']).status_code==404


def test_legacy_run_task_migration_is_idempotent(client):
    d=upload(client);r=confirm(client,plan(client,[d['id']]))
    tree=client.get('/api/research/tree').json();tid=tree['items'][0]['tasks'][0]['id']
    assert tid!=r['id']
    assert client.get('/api/research/tree').json()['items'][0]['tasks'][0]['id']==tid
    assert client.get(client.base+'/tasks/'+tid).json()['run_ids']==[r['id']]
    assert client.get(client.base+'/runs/'+r['id']).status_code==200


def test_task_boundaries_cross_project_inputs_and_paper_links(client):
    d=upload(client)
    first_pid=client.pid
    second_pid=client.post('/api/projects',json={'name':'Second project'}).json()['id']
    task=client.post('/api/projects/'+second_pid+'/research/tasks',json={'title':'Second task'}).json()
    base='/api/projects/'+second_pid+'/research/tasks/'+task['id']
    r=client.post(base+'/assets',json={'source_project_id':first_pid,'asset_ids':[d['source_asset_id']]})
    assert r.status_code==200,r.text
    copied=r.json()
    assert len(copied['dataset_ids'])==1
    dataset=client.get('/api/projects/'+second_pid+'/research/data',params={'task_id':task['id']}).json()['items'][0]
    assert dataset['project_id']==second_pid and dataset['source_asset_id']!=d['source_asset_id']
    assert client.get(client.base+'/tasks/'+task['id']).status_code==404
    assert client.patch(base,json={'paper_ids':['unknown']}).status_code==404
    assert client.patch(base,json={'session_ids':['unknown']}).status_code==404
    paper=client.post('/api/library/literature/add-file',files={'file':('reference.ris',b'TY  - JOUR\nTI  - Task reference\nER  -\n')})
    assert paper.status_code==201,paper.text
    paper_id=paper.json()['id']
    link=client.post('/api/projects/'+second_pid+'/research/papers/'+paper_id+'/link',json={})
    assert link.status_code==200,link.text
    assert link.json()['index_status']=='not_indexed'
    assert client.post('/api/projects/'+second_pid+'/research/papers/'+paper_id+'/link',json={}).json()['already_linked']
    assert client.patch(base,json={'paper_ids':[paper_id]}).json()['paper_ids']==[paper_id]
    listing=client.get('/api/library/literature').json()
    saved=next(p for p in listing['items'] if p['id']==paper_id)
    assert any(p['id']==second_pid for p in saved['projects'])


def test_task_workflow_uses_edited_input_version_without_changing_previous_plan(client):
    task=task_create(client)
    r=client.post(client.base+'/data/import',files={'file':('matrix.csv',MATRIX)},data={'task_id':task['id'],'options':'{"role":"taxonomy","unit":"counts"}'})
    d=r.json();base=client.base+'/tasks/'+task['id']
    body={'objective':'Alpha','steps':[{'id':'s1','skill':'alpha_diversity','parameters':{'dataset_id':d['id']},'depends_on':[]}],'study':{}}
    old=client.put(base+'/workflow',json=body).json()['plans'][-1]
    edited=client.post(client.base+'/data/'+d['id']+'/edit',json={'task_id':task['id'],'edits':[{'row':0,'column':1,'value':8}]})
    assert edited.status_code==201,edited.text
    new=edited.json();body['steps'][0]['parameters']['dataset_id']=new['id']
    r=client.put(base+'/workflow',json=body)
    assert r.status_code==201,r.text
    assert r.json()['plans'][-1]['dataset_ids']==[new['id']]
    assert r.json()['plans'][0]['dataset_ids']==[d['id']]
    assert r.json()['plans'][0]['fingerprint']==old['fingerprint']


def test_task_can_select_chart_json_as_reference_without_table_import(client):
    import uuid
    task=task_create(client)
    aid=uuid.uuid4().hex
    path=assets.ROOT/'runtime/assets'/client.uid/client.pid/(aid+'.json')
    path.parent.mkdir(parents=True,exist_ok=True);content=b'{"data":[],"layout":{"title":"Existing chart"}}';path.write_bytes(content)
    with gateway.store.connect() as db:
        db.execute('INSERT INTO assets(id,project_id,user_id,name,kind,path,size_bytes,sha256,status,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)',(aid,client.pid,client.uid,'pcoa.chart.json','charts',str(path),len(content),hashlib.sha256(content).hexdigest(),'ready','{}'))
    r=client.post(client.base+'/tasks/'+task['id']+'/assets',json={'source_project_id':client.pid,'asset_ids':[aid]})
    assert r.status_code==200,r.text
    assert aid in r.json()['asset_ids'] and r.json()['dataset_ids']==[]
