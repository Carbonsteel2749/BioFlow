import csv,io,json
from pathlib import Path
import pytest
from fastapi import APIRouter,FastAPI
from fastapi.testclient import TestClient
from backend.bioflow_platform import assets
from backend.bioflow_platform.plot_inputs import prepare_table,install_preparation
from backend.bioflow_platform.report_outputs import report_outputs
from backend.bioflow_platform.store import Store


def task_files(tmp_path,monkeypatch,kind='family'):
    monkeypatch.setattr(assets,'ROOT',tmp_path);base=tmp_path/'BioLLM_RawDataAnalysis/runtime/outputs/t';records=[]
    for sample,values in [('A',[10,20,70]),('B',[30,20,50])]:
        path=base/'nodes/taxonomy'/sample/'kraken.report';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('50\t500\t0\tG\t1\tignored genus\n'+''.join(f'10\t{value}\t0\tF\t{i+2}\t  F{i}\n' for i,value in enumerate(values)))
        records.append({'artifact_id':sample,'task_id':'t','artifact_type':'taxonomy.kraken_report','status':'active','sample_id':sample,'path':str(path),'file_name':path.name})
    assets.write_snapshot('t',{'id':'t'},records)
    return base


def test_family_stacked_preserves_other_and_does_not_rerun_analysis(tmp_path,monkeypatch):
    task_files(tmp_path,monkeypatch)
    data,meta=prepare_table('t','family','stacked',1)
    rows=list(csv.DictReader(io.StringIO(data.decode()),delimiter='\t'))
    assert sum(float(r['abundance']) for r in rows if r['sample']=='A')==100
    assert sum(float(r['abundance']) for r in rows if r['sample']=='B')==100
    assert {r['taxon'] for r in rows}=={'F2','Other taxa'}
    assert meta['recommended_parameters']['stacked_top_n']==2
    assert len(meta['sources'])==2 and all(r['sha256'] for r in meta['sources'])


def test_family_pca_matrix_keeps_raw_counts_and_exposes_clr_parameter(tmp_path,monkeypatch):
    task_files(tmp_path,monkeypatch)
    data,meta=prepare_table('t','family','pca',0)
    rows=list(csv.DictReader(io.StringIO(data.decode()),delimiter='\t'))
    assert rows[0]['F0']=='10' and rows[1]['F0']=='30'
    assert meta['recommended_parameters']['pca_transform']=='clr'
    assert meta['feature_count']==3


def test_missing_report_tables_are_discovered_once_with_relative_identity(tmp_path,monkeypatch):
    monkeypatch.setattr(assets,'ROOT',tmp_path);folder=tmp_path/'BioLLM_RawDataAnalysis/runtime/outputs/t/nodes/report';(folder/'report').mkdir(parents=True);(folder/'tables').mkdir()
    (folder/'report/summary.json').write_text(json.dumps({'task_id':'t'}))
    for name in ['gene_families.tsv','pathway_abundance.tsv','read_counts.tsv']:(folder/'tables'/name).write_text('sample_id\tx\nA\t1\n')
    results=report_outputs('t',[]);assert len(results)==3
    assert report_outputs('t',results)==[]
    assert len({r['artifact_id'] for r in results})==3 and all(r['sha256'] for r in results)


def test_functional_preparation_excludes_specials_and_report_copies(tmp_path,monkeypatch):
    monkeypatch.setattr(assets,'ROOT',tmp_path);base=tmp_path/'BioLLM_RawDataAnalysis/runtime/outputs/t';records=[]
    for sample in ['A','B']:
        p=base/'nodes/function'/sample/'read_ko.tsv';p.parent.mkdir(parents=True,exist_ok=True);p.write_text('sample_id\tfunction_id\tabundance\tanalysis_scope\n'+f'{sample}\tUNMAPPED\t100\tcommunity_total\n{sample}\tK01\t3\tcommunity_total\n{sample}\tK02\t5\tcommunity_total\n')
        records.append({'artifact_id':sample,'status':'active','artifact_type':'functional.ko_abundance','sample_id':sample,'path':str(p),'file_name':p.name})
    assets.write_snapshot('t',{},records)
    data,meta=prepare_table('t','ko','heatmap',2)
    assert b'UNMAPPED' not in data and b'K01' in data
    assert meta['excluded_special'] and meta['sample_count']==2


def test_preparation_cannot_read_another_accounts_task(tmp_path,monkeypatch):
    monkeypatch.setattr(assets,'ROOT',tmp_path);store=Store(tmp_path/'db.sqlite3');pid=store.create('P','',owner_id='u')['id'];store.claim('workflow','t','u');store.link(pid,'workflow','t');app=FastAPI()
    @app.middleware('http')
    async def user(request,call_next):request.state.user={'id':'other'};return await call_next(request)
    router=APIRouter(prefix='/p/{pid}');install_preparation(router,lambda:store,lambda p,r:None,None);app.include_router(router);client=TestClient(app)
    assert client.get(f'/p/{pid}/plot-inputs?task_id=t').status_code==404
    assert client.post(f'/p/{pid}/plot-inputs',json={'task_id':'t','chart':'pca','kind':'family'}).status_code==404
