import hashlib
import importlib.util
import json
import sys
from pathlib import Path
import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.bioflow_platform import analysis_setup
from backend.bioflow_platform.store import Store


@pytest.fixture
def setup_client(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('analysis_test_schemas',Path('BioLLM_RawDataAnalysis/backend/app/schemas.py'))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    store=Store(tmp_path/'portal.sqlite3');store.claim('dataset','study','owner');store.link('project','dataset','study')
    root=tmp_path/'analysis';(root/'config').mkdir(parents=True)
    records={}
    for group,keys in {'taxonomy_reads':['kraken2','bracken'],'function_reads':['humann_nucleotide','humann_protein','metaphlan','humann_utility','ko_mapping','ec_mapping'],'mag_annotation':['classification','function']}.items():
        records[group]={}
        for key in keys:
            folder=tmp_path/'databases'/key;folder.mkdir(parents=True)
            name='database150mers.kmer_distrib' if key=='kraken2' else 'required.txt'
            (folder/name).write_bytes(b'valid reference')
            digest=hashlib.sha256(f'{name}\t15\n'.encode()).hexdigest()
            records[group][key]={'database_name':key,'path':str(folder),'release':'v1','purpose':group,'tool_compatibility_version':'test','required_sentinel_files':[name],'manifest_basis':'SHA256 of the sorted top-level file-name and byte-size inventory','manifest_sha256':digest}
    (root/'config/database-registry.local.json').write_text(json.dumps({'profiles':{'test-profile':records}}))
    host=tmp_path/'host'/'GRCh38';host.parent.mkdir()
    for part in ('1','2','3','4','rev.1','rev.2'):Path(f'{host}.{part}.bt2').write_bytes(b'index')
    monkeypatch.setenv('BIOFLOW_HOST_INDEX',str(host));monkeypatch.setattr(analysis_setup,'analysis_root',lambda:root)
    calls=[]
    defaults={k:records[g][n]['path'] for k,(g,n) in analysis_setup.REFERENCE_KEYS.items()};defaults['host_index']=str(host)
    async def call(service,path,method='GET',payload=None):
        calls.append((path,method,payload))
        if path=='/api/methods/parameters':return {'parameters_schema':module.WorkflowParameters.model_json_schema()['properties']}
        if path=='/api/capabilities':return {'database_profile':'test-profile','reads_analysis':True,'mag_analysis':True}
        if path=='/api/datasets/study':return {'id':'study','name':'Study','sample_count':2,'status':'available','reason':None}
        if path.endswith('/reuse'):return {'manifest_path':'/inputs/study.csv'}
        if path=='/api/tasks/review':
            try:params=module.WorkflowParameters.model_validate(payload['parameters']).model_dump()
            except ValidationError as exc:raise HTTPException(422,str(exc))
            return {'parameters':{**params,**defaults},'sample_count':2,'groups':{'version':1}}
        if path=='/api/tasks':return {'id':'created-task','parameters':payload['parameters']}
        raise AssertionError(path)
    app=FastAPI()
    @app.middleware('http')
    async def user(request,call_next):
        request.state.user={'id':request.headers.get('x-owner','owner')};return await call_next(request)
    def check(pid,request):
        if pid!='project':raise HTTPException(404,'project')
    router=APIRouter(prefix='/api/projects/{pid}');analysis_setup.install_analysis(router,lambda:store,check,call);app.include_router(router)
    client=TestClient(app);client.base='/api/projects/project/analysis';client.store=store;client.records=records;client.calls=calls;client.root=root
    client.body={'name':'Study analysis','dataset_id':'study','manifest_path':'/inputs/study.csv','parameters':{'threads':8,'fastp_qualified_quality_phred':25,'fastp_correction':True,'fastp_length_required':75,'host_filter_mode':'concordant_unmapped','host_max_removed_pct':80,'read_length':150}}
    return client


def test_exact_parameters_review_and_project_submission(setup_client):
    c=setup_client;options=c.get(c.base+'/options');assert options.status_code==200
    assert all(r['ready'] is None for r in options.json()['references']['items'])
    assert c.get(c.base+'/datasets').json()['items'][0]['id']=='study'
    result=c.post(c.base+'/check',json=c.body);assert result.status_code==200,result.text
    assert result.json()['ready'],result.text
    params=result.json()['review']['parameters'];assert params['fastp_correction'] is True and params['fastp_qualified_quality_phred']==25
    assert not any(path=='/api/tasks' for path,_,_ in c.calls)
    started=c.post(c.base+'/start',json={**result.json()['request'],'review_token':result.json()['review_token']});assert started.status_code==201,started.text
    assert started.json()['parameters']['host_filter_mode']=='concordant_unmapped'
    assert c.store.owns('task','created-task','owner') and c.store.ids('project','task')==['created-task']
    assert c.post(c.base+'/check',json=c.body,headers={'x-owner':'other'}).status_code==404
    wrong={**c.body,'manifest_path':'/inputs/foreign.csv'}
    assert c.post(c.base+'/start',json={**wrong,'review_token':result.json()['review_token']}).status_code==422


def test_missing_and_changed_references_block_start(setup_client):
    c=setup_client;approved=c.post(c.base+'/check',json=c.body).json();folder=Path(c.records['function_reads']['humann_protein']['path']);(folder/'required.txt').unlink()
    report=c.post(c.base+'/check',json=c.body).json();assert not report['ready'] and any('必需文件缺失' in e for e in report['errors'])
    assert c.post(c.base+'/start',json={**approved['request'],'review_token':approved['review_token']}).status_code==422
    assert not any(path=='/api/tasks' for path,_,_ in c.calls)
    (folder/'required.txt').write_bytes(b'changed content and size')
    assert any('清单校验值' in e for e in c.post(c.base+'/check',json=c.body).json()['errors'])


def test_host_selection_read_length_and_parameter_validation(setup_client):
    c=setup_client
    for key,value in [('fastp_qualified_quality_phred',0),('host_max_removed_pct',101),('threads',257)]:
        body={**c.body,'parameters':{**c.body['parameters'],key:value}}
        assert not c.post(c.base+'/check',json=body).json()['ready']
    body={**c.body,'parameters':{**c.body['parameters'],'host_index':'/unregistered/index'}}
    assert c.post(c.base+'/check',json=body).status_code==422
    body={**c.body,'parameters':{**c.body['parameters'],'read_length':100}}
    assert any('100 bp' in e for e in c.post(c.base+'/check',json=body).json()['errors'])
    body={**c.body,'parameters':{**c.body['parameters'],'unknown':True}}
    assert c.post(c.base+'/check',json=body).status_code==422


def test_resolved_registration_mismatch_blocks(setup_client):
    c=setup_client;path=c.root/'runtime/config';path.mkdir(parents=True)
    resolved=json.loads(json.dumps(c.records));resolved['taxonomy_reads']['kraken2']['release']='old-version'
    (path/'database.resolved.json').write_text(json.dumps({'database_profile':'test-profile','databases':resolved}))
    assert any('已解析数据库清单' in e for e in c.post(c.base+'/check',json=c.body).json()['errors'])


def test_changed_version_requires_new_confirmation(setup_client):
    c=setup_client;approved=c.post(c.base+'/check',json=c.body).json()
    registry=c.root/'config/database-registry.local.json'
    changed=json.loads(registry.read_text());changed['profiles']['test-profile']['function_reads']['humann_protein']['release']='v2'
    registry.write_text(json.dumps(changed))
    result=c.post(c.base+'/start',json={**approved['request'],'review_token':approved['review_token']})
    assert result.status_code==409,result.text
    assert not any(path=='/api/tasks' for path,_,_ in c.calls)
