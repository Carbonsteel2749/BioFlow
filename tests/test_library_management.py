import csv,hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import APIRouter,FastAPI
from fastapi.testclient import TestClient
from backend.bioflow_platform import assets
from backend.bioflow_platform.store import Store
from backend.bioflow_platform.personal_library import install_library as install

@pytest.fixture
def setup(tmp_path,monkeypatch):
 monkeypatch.setattr(assets,'ROOT',tmp_path);store=Store(tmp_path/'db.sqlite3');pid=store.create('P','',owner_id='u')['id'];store.claim('workflow','t','u');store.link(pid,'workflow','t')
 p=tmp_path/'BioLLM_RawDataAnalysis/runtime/outputs/t/nodes/WF_TAXONOMY_ABCDEF/taxonomy/result.tsv';p.parent.mkdir(parents=True);p.write_text('sample\tvalue\nA\t2\nB\t3\n')
 records=[{'id':'artifact:a','artifact_id':'a','kind':'results','name':'result.tsv','status':'ready','size_bytes':p.stat().st_size,'task_ids':['t'],'relative_path':p.relative_to(tmp_path/'BioLLM_RawDataAnalysis/runtime/outputs').as_posix(),'storage_root':'analysis_outputs'}]
 async def catalogue(pid,user):return [dict(r) for r in records]
 app=FastAPI()
 @app.middleware('http')
 async def user(request,call_next):request.state.user={'id':request.headers.get('test-user','u')};return await call_next(request)
 service=SimpleNamespace(catalogue=catalogue);app.state.files=service;router=APIRouter(prefix='/p/{pid}');install(app,router,lambda:store,lambda p,r:None,service);app.include_router(router)
 return TestClient(app),store,pid,records,p

def test_process_archive_and_manual_override_survive_refresh(setup):
 c,store,pid,records,p=setup;url=f'/p/{pid}'
 d=c.get(url+'/library-browser?task_id=t&kind=results').json();assert d['folders'][0]['name']=='物种分类';fid=d['folders'][0]['id'];assert d['items']==[]
 assert len(c.get(url+f'/library-browser?task_id=t&kind=results&folder_id={fid}').json()['items'])==1
 new=c.post(url+'/library-folders',json={'task_id':'t','kind':'results','name':'我整理的文件'}).json()['id']
 assert c.post(url+'/library-folders/move',json={'task_id':'t','kind':'results','folder_id':new,'asset_ids':['artifact:a']}).status_code==200
 assert len(c.get(url+f'/library-browser?task_id=t&kind=results&folder_id={new}').json()['items'])==1
 assert c.get(url+'/library-browser?task_id=t&kind=charts').json()['folders']==[]

def test_folder_category_and_account_boundaries(setup):
 c,store,pid,records,p=setup;url=f'/p/{pid}';body={'task_id':'t','kind':'data','name':'Uploads'};fid=c.post(url+'/library-folders',json=body).json()['id']
 assert c.post(url+'/library-folders/move',json={'task_id':'t','kind':'results','folder_id':fid,'asset_ids':['artifact:a']}).status_code==404
 assert c.get(url+'/library-browser?task_id=t',headers={'test-user':'other'}).status_code==404
 assert c.post(url+'/library-folders',json={**body,'name':'../bad'}).status_code==422

def test_preview_is_bounded_and_scoped(setup):
 c,store,pid,records,p=setup;url=f'/p/{pid}/library-preview?task_id=t&asset_id=artifact:a'
 d=c.get(url).json();assert d['mode']=='table' and d['rows'][0]==['A','2']
 p.write_bytes(b'x'*(6*1024**2));d=c.get(url).json();assert d['mode']=='unavailable' and '上限' in d['reason']
 assert c.get(url.replace('artifact:a','artifact:other')).status_code==404

def test_personal_add_stores_without_project_or_vector_index(setup):
 c,store,pid,records,p=setup
 r=c.post('/api/library/literature/add-file',files={'file':('paper.md',b'# Study\nEvidence','text/markdown')});assert r.status_code==201;aid=r.json()['id']
 with store.connect() as db:
  row=db.execute('SELECT * FROM personal_documents WHERE id=?',(aid,)).fetchone();assert json.loads(row['metadata'])['index_status']=='not_indexed';assert (assets.ROOT/'runtime/personal-library'/row['path']).read_bytes()==b'# Study\nEvidence';assert db.execute('SELECT count(*) FROM assets').fetchone()[0]==0
 assert c.post('/api/library/literature/add-file',files={'file':('paper.md',b'# Study\nEvidence')}).json()['id']==aid
 assert c.post('/api/library/literature/add-url',json={'url':'javascript:alert(1)'}).status_code==422
 body={'url':'https://example.org/paper','title':'Study'};r=c.post('/api/library/literature/add-url',json=body);assert r.status_code==201
 assert c.post('/api/library/literature/add-url',json=body).json()['already_saved']

def test_user_folders_can_rename_and_only_delete_when_empty(setup):
 c,store,pid,records,p=setup;url=f'/p/{pid}';body={'task_id':'t','kind':'results','name':'手动整理'}
 fid=c.post(url+'/library-folders',json=body).json()['id']
 assert c.patch(url+'/library-folders/'+fid,json={**body,'name':'新名称'}).status_code==200
 c.post(url+'/library-folders/move',json={'task_id':'t','kind':'results','folder_id':fid,'asset_ids':['artifact:a']})
 assert c.delete(url+f'/library-folders/{fid}?task_id=t&kind=results').status_code==409
 c.post(url+'/library-folders/move',json={'task_id':'t','kind':'results','folder_id':'','asset_ids':['artifact:a']})
 assert c.delete(url+f'/library-folders/{fid}?task_id=t&kind=results').status_code==200


def test_editing_personal_title_preserves_link_and_index_state(setup):
 c,store,pid,records,p=setup
 aid=c.post('/api/library/literature/add-url',json={'url':'https://example.org/article','title':'Old'}).json()['id']
 assert c.patch('/api/library/literature/'+aid,json={'title':'New','tags':'Edited tag'}).status_code==200
 with store.connect() as db:m=json.loads(db.execute('SELECT metadata FROM personal_documents WHERE id=?',(aid,)).fetchone()['metadata'])
 assert m['url']=='https://example.org/article' and m['index_status']=='not_indexed' and m['title']=='New'


def test_new_analysis_artifact_is_synced_into_process_folder(setup):
 import os
 from backend.bioflow_platform import catalogue
 c,store,pid,records,p=setup
 def artifact(aid,name):return {'artifact_id':aid,'task_id':'t','artifact_type':'taxonomy.species_abundance','status':'active','path':str(p.parent/name),'file_name':name,'size_bytes':10,'sha256':'a'*64,'media_type':'text/tab-separated-values'}
 outputs=[artifact('a','result.tsv')]
 async def call(service,path):return list(outputs) if '/artifacts?' in path else {'id':'t','name':'Analysis','status':'succeeded'}
 async def live_catalogue(pid,user):return await catalogue.catalogue(c.app.state.files,pid,user)
 c.app.state.files.call=call;c.app.state.files.get_store=lambda:store;c.app.state.files.catalogue=live_catalogue
 url=f'/p/{pid}/library-browser?task_id=t&kind=results'
 first=c.get(url).json();assert sum(f['count'] for f in first['folders'])==1
 outputs.append(artifact('b','new-result.tsv'));os.utime(assets.snapshot_path('t'),(0,0))
 second=c.get(url).json();assert sum(f['count'] for f in second['folders'])==2
 assert {f['id'] for f in first['folders']}=={f['id'] for f in second['folders']}
