import json
from pathlib import Path
import uuid

from deploy import server96
from backend.bioflow_platform.store import Store


def test_startup_preserves_portable_dataset_ids_and_model_config(tmp_path,monkeypatch):
    monkeypatch.setattr(server96,'ROOT',tmp_path)
    monkeypatch.setattr(server96,'RUNTIME',tmp_path/'runtime')
    monkeypatch.setattr(server96,'ANALYSIS',tmp_path/'original-analysis')
    for name in ('BIOFLOW_ANALYSIS_ROOT','BIOFLOW_ANALYSIS_URL','BIOFLOW_WRITING_URL','BIOFLOW_LITERATURE_URL','BIOFLOW_QWEN_URL','BIOFLOW_DATABASE','ARTICLE_WRITING_ANALYSIS_ROOT'):
        monkeypatch.setenv(name,'test')
    store=Store(tmp_path/'runtime/bioflow.sqlite3')
    project=store.create('Study','kept','owner')
    store.claim('task','kept-task','owner');store.link(project['id'],'task','kept-task')
    store.claim('dataset','windows-id','owner');store.link(project['id'],'dataset','windows-id')
    manifest='/home/xh/BioLLM_RawDataAnalysis/runtime/incoming/manifests/samples.csv'
    expected=uuid.uuid5(uuid.NAMESPACE_URL,'biollm-dataset:'+manifest).hex
    def fetch(url,timeout=4):
        if url.endswith('/api/tasks/kept-task'):return {'manifest_path':manifest}
        assert url.endswith('/api/datasets/'+expected)
        return {'name':'Original samples'}
    monkeypatch.setattr(server96,'fetch',fetch)
    server96.configure()
    assert store.ids(project['id'],'dataset')==['windows-id']
    assert store.owns('dataset','windows-id','owner')
    assert len(list((tmp_path/'runtime/backups').glob('*.sqlite3')))==1
    config=tmp_path/'article_writing/outputs/ui-config/llm.json'
    assert json.loads(config.read_text())['url']=='http://127.0.0.1:11434'
    config.write_text('{"url":"http://custom:11434"}')
    server96.configure()
    assert json.loads(config.read_text())['url']=='http://custom:11434'


def test_analysis_root_maps_server_outputs(tmp_path,monkeypatch):
    from backend.bioflow_platform import assets
    monkeypatch.setenv('BIOFLOW_ANALYSIS_ROOT',str(tmp_path/'original'))
    path=assets.local_output('/home/xh/BioLLM_RawDataAnalysis/runtime/outputs/task-a/chart.png')
    assert path==(tmp_path/'original/runtime/outputs/task-a/chart.png').resolve()
    assert assets.local_output('/home/xh/BioLLM_RawDataAnalysis/runtime/outputs/../../outside.txt') is None


def test_check_reports_existing_services_and_required_model(tmp_path,monkeypatch):
    monkeypatch.setattr(server96,'RUNTIME',tmp_path)
    monkeypatch.setattr(server96,'ANALYSIS',tmp_path)
    for folder in ('runtime/incoming','runtime/outputs'):(tmp_path/folder).mkdir(parents=True)
    for filename in ('backend/app/main.py','runtime/state/tasks.sqlite3'):
        path=tmp_path/filename;path.parent.mkdir(parents=True,exist_ok=True);path.touch()
    (tmp_path/'bioflow.sqlite3').touch()
    monkeypatch.setattr(server96,'healthy',lambda url:True)
    monkeypatch.setattr(server96,'fetch',lambda url:{'models':[{'name':'qwen3:14b'}]})
    assert server96.check()['ready']
    monkeypatch.setattr(server96,'fetch',lambda url:{'models':[{'name':'qwen3:4b'}]})
    report=server96.check()
    assert not report['ready']
    assert next(r for r in report['checks'] if r['name']=='Qwen3-14B')['ok'] is False
