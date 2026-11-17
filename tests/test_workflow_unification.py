import asyncio
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from bioflow_platform import unified_workflows as u

REG={'nodes':[{'type':t,'parameters_schema':p} for t,p in [('fastq_input',{}),('fastp',{'threads':{'type':'integer','default':4}})]]}
def design(threads=3):
 return {'name':'清洗','nodes':[{'id':'input','type':'fastq_input','parameters':{'dataset_id':'do-not-publish'}},{'id':'trim','type':'fastp','parameters':{'threads':threads}}], 'edges':[{'id':'e1','source':'input','sourcePort':'reads','target':'trim','targetPort':'reads','acknowledged':False}]}

def test_compile_keeps_node_identity_and_independent_parameters():
 g=design(); g['nodes'].append({'id':'trim2','type':'fastp','parameters':{'threads':2}})
 result=u.execution_graph(g,REG)
 assert result['nodes'][1:]==[{'id':'trim','type':'fastp','parameters':{'threads':3}},{'id':'trim2','type':'fastp','parameters':{'threads':2}}]
 assert result['edges'][0]['source_node']=='input'
 assert result['nodes'][0]['parameters']=={}

def test_unknown_custom_and_unsupported_methods_fail_not_silently_drop():
 g=design();g['nodes'][1]['custom']=[{'name':'flag','value':1}]
 with pytest.raises(ValueError):u.execution_graph(g,REG)

def test_confirmed_risks_reach_engine_contract():
 g=design();g['nodes'][1]['type']='taxonomy';g['edges'][0]['acknowledged']=True
 registry={'nodes':[{'type':'fastq_input','parameters_schema':{},'outputs':[{'id':'reads','data_type':'paired_raw_reads'}]},{'type':'taxonomy','parameters_schema':{'threads':{}}}]}
 assert u.execution_graph(g,registry)['accepted_risks']==['risk:trim:annotation_without_host_depletion']

def test_generic_proxy_cannot_enumerate_other_users_templates(tmp_path):
 from bioflow_platform.store import Store
 from bioflow_platform.access import process_json
 store=Store(tmp_path/'proxy.sqlite3');store.claim('workflow_template','wf-private','alice')
 data=[{'template_id':'builtin-qc'},{'template_id':'wf-private'}]
 assert process_json(store,'analysis','api/workflows/templates','GET',data,{'id':'bob','role':'user'})==[{'template_id':'builtin-qc'}]
 g=design();g['nodes'][1]['type']='dada2'
 with pytest.raises(ValueError):u.execution_graph(g,REG)

def test_release_and_task_snapshot_are_immutable(tmp_path):
 from bioflow_platform.store import Store
 store=Store(tmp_path/'state.sqlite3');u.initialize(store)
 u.save_release(store,'alice',{'template_id':'wf-a','version':1,'workflow':{'nodes':[]}},design())
 u.save_release(store,'alice',{'template_id':'wf-a','version':2,'workflow':{'nodes':[]}},design(7))
 assert u.release(store,'alice','wf-a',1)['design']['nodes'][1]['parameters']['threads']==3
 with pytest.raises(Exception):u.save_release(store,'alice',{'template_id':'wf-a','version':1,'workflow':{'nodes':[]}},design(9))
 with pytest.raises(KeyError):u.release(store,'bob','wf-a',1)
 record=u.release(store,'alice','wf-a',1)
 u.save_snapshot(store,'alice','p1','run1',record,record['record']['workflow'])
 snap=u.snapshot(store,'alice','p1','run1')
 assert snap['version']==1
 assert snap['design']['nodes'][1]['parameters']['threads']==3
 with pytest.raises(KeyError):u.snapshot(store,'alice','other','run1')

def test_gateway_publishes_versions_and_submits_selected_snapshot(tmp_path):
 from fastapi import FastAPI,APIRouter
 from fastapi.testclient import TestClient
 from bioflow_platform.store import Store
 import copy
 store=Store(tmp_path/'state.sqlite3');store.claim('dataset','d1','alice');store.link('p1','dataset','d1')
 records=[];submissions=[]
 registry=copy.deepcopy(REG);registry['nodes'][1]['parameters_schema']['quality']={'type':'integer','default':20}
 async def upstream(service,path,method='GET',payload=None):
  if path.endswith('/registry'):return registry
  if path=='/api/workflows/templates' and method=='POST':
   r={**payload,'template_id':'wf-test','version':1};records.append(copy.deepcopy(r));return r
  if path=='/api/workflows/templates/wf-test' and method=='PUT':
   r={**payload,'template_id':'wf-test','version':payload['base_version']+1};records.append(copy.deepcopy(r));return r
  if path=='/api/datasets/d1/reuse':return {'manifest_path':'/owned/samples.csv'}
  if path=='/api/workflows/runs' and method=='POST':
   submissions.append(copy.deepcopy(payload));return {'task_id':'run-'+str(len(submissions)),'status':'queued'}
  if path.startswith('/api/workflows/runs/'):return {'task_id':path.split('/')[-1],'status':'completed'}
  raise AssertionError(path)
 app=FastAPI();router=APIRouter(prefix='/api/projects/{pid}')
 @app.middleware('http')
 async def user(request,call_next):request.state.user={'id':'alice'};return await call_next(request)
 u.install(router,lambda:store,lambda pid,request:None,upstream);app.include_router(router)
 client=TestClient(app);base='/api/projects/p1/analysis/workflows'
 r=client.post(base+'/publish',json={'design':design()});assert r.status_code==201,r.text
 r=client.post(base+'/publish',json={'design':design(8),'template_id':'wf-test','base_version':1});assert r.status_code==201,r.text
 r=client.post(base+'/start',json={'template_id':'wf-test','version':1,'name':'old version','dataset_id':'d1','node_parameters':{'trim':{'threads':5}}})
 assert r.status_code==201,r.text
 assert submissions[0]['workflow']['nodes'][1]['parameters']['threads']==5
 snap=client.get(base+'/runs/run-1').json()['snapshot']
 assert snap['version']==1 and snap['workflow']['nodes'][1]['parameters']['threads']==5
 assert snap['workflow']['nodes'][1]['parameters']['quality']==20
 registry['nodes'][1]['parameters_schema']['quality']['default']=30
 assert client.get(base+'/runs/run-1').json()['snapshot']['parameter_registry']['nodes'][1]['parameters_schema']['quality']['default']==20
 assert snap['record']['workflow']['nodes'][1]['parameters']['threads']==3
 r=client.post(base+'/start',json={'template_id':'wf-test','version':1,'name':'bad','dataset_id':'other'})
 assert r.status_code==404
 assert len(submissions)==1
