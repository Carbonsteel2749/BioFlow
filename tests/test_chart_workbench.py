import asyncio,hashlib,json
from pathlib import Path
import pytest
from fastapi import FastAPI,APIRouter
from fastapi.testclient import TestClient
from backend.bioflow_platform import assets
from backend.bioflow_platform.store import Store
from backend.bioflow_platform.chart_display import prepare


def test_grouping_join_uses_identifiers_and_snapshot_integrity(tmp_path):
    data=b'id,cohort\nB,case\nA,control\n';(tmp_path/'grouping.csv').write_bytes(data)
    spec={'chart':'pca','grouping_sha256':hashlib.sha256(data).hexdigest(),'parameters':{'sample_column':'id','group_column':'cohort'}}
    rows=prepare([{'sample':'A','x':'1','y':'2'},{'sample':'B','x':'3','y':'4'}],spec,tmp_path)
    assert [r['group'] for r in rows]==['control','case']
    (tmp_path/'grouping.csv').write_bytes(data+b'C,case\n')
    with pytest.raises(ValueError,match='快照校验'):prepare(rows,spec,tmp_path)


def test_missing_group_fails_instead_of_silently_dropping_samples(tmp_path):
    data=b'sample,group\nA,one\n';(tmp_path/'grouping.csv').write_bytes(data)
    spec={'chart':'pca','grouping_sha256':hashlib.sha256(data).hexdigest(),'parameters':{}}
    with pytest.raises(ValueError,match='B'):prepare([{'sample':'B','x':1,'y':2}],spec,tmp_path)


def test_expression_columns_and_long_abundance_are_real_matrix_conversions(tmp_path):
    rows=prepare([{'gene':'G1','A':'1','B':'2'},{'gene':'G2','A':'3','B':'4'}],{'chart':'pca','parameters':{'matrix_orientation':'samples_columns'}},tmp_path)
    assert rows==[{'sample':'A','G1':'1','G2':'3'},{'sample':'B','G1':'2','G2':'4'}]
    rows=prepare([{'sample':'A','taxon':'T','v':'2'},{'sample':'A','taxon':'T','v':'3'},{'sample':'B','taxon':'Z','v':'1'}],{'chart':'pca','parameters':{'matrix_format':'long','feature_column':'taxon','value_column':'v'}},tmp_path)
    assert rows[0]['T']==5 and rows[1]['T']==0 and rows[1]['Z']==1


def test_workflow_results_use_workflow_metadata_and_exact_relative_path(tmp_path,monkeypatch):
    from backend.bioflow_platform.catalogue import sync_task_snapshot,task_rows
    monkeypatch.setattr(assets,'ROOT',tmp_path);store=Store(tmp_path/'db.sqlite3');p=store.create('P','',owner_id='u');pid=p['id']
    store.claim('workflow','w','u');store.link(pid,'workflow','w');calls=[]
    class Service:
        async def call(self,kind,path):
            calls.append(path)
            if path=='/api/workflows/runs/w':return {'task_id':'w','name':'W','status':'succeeded','input_manifest':''}
            if path=='/api/tasks/w/artifacts?metadata_only=true':return [{'artifact_id':'a','task_id':'w','source_file':'nodes/report/table.tsv','file_name':'table.tsv','status':'active','artifact_type':'table','media_type':'text/tab-separated-values','size_bytes':10,'sha256':'x'}]
            raise AssertionError('Wrong legacy endpoint: '+path)
    asyncio.run(sync_task_snapshot(Service(),store,pid,'u','w'))
    assert assets.read_snapshot('w')['artifacts'][0]['path']=='w/nodes/report/table.tsv'
    assert task_rows(store,pid,'u')[0]['task_id']=='w'
    assert '/api/tasks/w' not in calls


def test_task_sources_filtered_and_cross_task_creation_rejected(tmp_path,monkeypatch):
    from backend.bioflow_platform.project_figures import install_figures
    monkeypatch.setattr(assets,'ROOT',tmp_path);store=Store(tmp_path/'db.sqlite3');pid=store.create('P','',owner_id='u')['id']
    for tid in ('a','b'):store.claim('workflow',tid,'u');store.link(pid,'workflow',tid)
    for aid,tid in [('one','a'),('two','b')]:
        data=b'sample,f1,f2\nA,1,2\nB,2,3\n';path=tmp_path/'runtime/assets/u'/pid/(aid+'.csv');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        with store.connect() as db:db.execute('INSERT INTO assets(id,project_id,user_id,name,kind,path,size_bytes,sha256,status,metadata) VALUES(?,?,?,?,?,?,?,?,?,?)',(aid,pid,'u',aid+'.csv','data',f'u/{pid}/{aid}.csv',len(data),hashlib.sha256(data).hexdigest(),'ready',json.dumps({'task_id':tid})))
    app=FastAPI()
    @app.middleware('http')
    async def auth(request,call_next):request.state.user={'id':'u','role':'developer'};return await call_next(request)
    router=APIRouter(prefix='/p/{pid}');install_figures(router,lambda:store,lambda pid,request:None);app.include_router(router)
    client=TestClient(app);r=client.get(f'/p/{pid}/figure-sources?task_id=a');assert r.status_code==200
    assert [r['id'] for r in r.json()['items']]==['one']
    r=client.post(f'/p/{pid}/figures',json={'chart':'pca','source_id':'two','task_id':'a','parameters':{}})
    assert r.status_code==422 and '不属于当前任务' in r.text
    # A descendant must not silently accept altered grouping bytes from its parent.
    source={'id':'one','name':'one.csv','sha256':hashlib.sha256(data).hexdigest(),'task_id':'a'}
    parent=tmp_path/'runtime/figures/u'/pid/'parent';parent.mkdir(parents=True)
    (parent/'source.csv').write_bytes(data);(parent/'grouping.csv').write_bytes(b'sample,group\nA,tampered\nB,case\n')
    (parent/'request.json').write_text(json.dumps({'columns':{},'grouping_sha256':'original-hash'}))
    with store.connect() as db:db.execute('INSERT INTO project_figures(id,project_id,user_id,chart,status,parameters,source_json) VALUES(?,?,?,?,?,?,?)',('parent',pid,'u','pca','completed','{}',json.dumps(source)))
    r=client.post(f'/p/{pid}/figures',json={'chart':'pca','parent_id':'parent','task_id':'a','parameters':{}})
    assert r.status_code==409 and '分组快照校验失败' in r.text


def test_svg_edits_and_fonts_survive_all_exports(tmp_path):
    pytest.importorskip('matplotlib')
    from backend.bioflow_platform.chart_runner import render
    data=b'sample,f1,f2,group\nA,1,2,one\nB,2,1,one\nC,5,8,two\n';(tmp_path/'source.csv').write_bytes(data)
    spec={'chart':'pca','source':{'sha256':hashlib.sha256(data).hexdigest()},'parameters':{'title':'Edited title','title_size':25,'legend_size':13,'title_x':.4,'title_y':1.1,'legend_x':1.05,'legend_y':.9,'legend_labels':{'group:one':'Edited legend'},'series_colors':{'group:one':'#125634'}}}
    (tmp_path/'request.json').write_text(json.dumps(spec));render(tmp_path)
    svg=(tmp_path/'figure.svg').read_text();assert 'bf-title' in svg and 'bf-plot-area' in svg and 'Edited title' in svg and 'Edited legend' in svg and '#125634' in svg
    assert all((tmp_path/('figure.'+ext)).stat().st_size>100 for ext in ('svg','png','pdf'))
