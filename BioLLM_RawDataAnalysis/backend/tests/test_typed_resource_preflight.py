from types import SimpleNamespace
from backend.app.services.input_datasets import InputDatasetService
from backend.app.services.resource_preflight import ResourcePreflight


def test_typed_preflight_uses_frozen_sizes_not_csv_or_full_hash(tmp_path,monkeypatch):
    from backend.app.services import resource_preflight as module
    settings=SimpleNamespace(input_root=tmp_path/'input',state_root=tmp_path/'state',minimum_free_disk_bytes=10)
    service=InputDatasetService(settings);folder=service.root/'one';folder.mkdir(parents=True)
    (folder/'c.fa').write_bytes(b'>c\nACGT\n')
    service.register('one',{'kind':'contigs','cohort_id':'c','fasta':'c.fa'})
    path=service.snapshot_metadata_only({'a':'one'},'run1')
    def forbidden(*args):raise AssertionError('must not read CSV or full data SHA before admission')
    monkeypatch.setattr(ResourcePreflight,'_manifest_size',forbidden)
    monkeypatch.setattr(InputDatasetService,'get',forbidden)
    monkeypatch.setattr(module,'reserved_upload_bytes',lambda s:0)
    monkeypatch.setattr(module,'_filesystem_snapshot',lambda p:{'filesystem_id':'one','free_bytes':12,'total_bytes':100})
    report=ResourcePreflight(settings).inspect(typed_snapshot=path)
    assert report['input']['source']=='server_typed_snapshot'
    assert report['input']['bytes']==8
    assert report['estimate']['private_input_copy_bytes']==8
    assert not report['can_submit']


def test_oversize_snapshot_becomes_request_validation_error(tmp_path,monkeypatch):
    import pytest
    from pathlib import Path
    from backend.app.config import Settings
    from backend.app.services import input_datasets as module
    from backend.app.services.workflow_runs import WorkflowRunService,WorkflowRunValidationError
    from backend.app.services.tasks import TaskService
    from backend.app.workflows.state import WorkflowRunRepository
    from backend.tests.test_typed_execution import graph,PROJECT
    settings=Settings(input_root=tmp_path/'input',state_root=tmp_path/'state',workflow_path=PROJECT/'workflow/main.nf')
    settings.prepare();service=InputDatasetService(settings)
    folder=service.root/'matrix';folder.mkdir(parents=True)
    (folder/'values.tsv').write_bytes(b'sample_id\tfeature_id\tvalue\ns1\tK1\t4\n')
    service.register('matrix',{'kind':'matrix','table':'values.tsv','feature_type':'KO','unit':'counts','normalization':'none'})
    repo=WorkflowRunRepository(settings.database_path);repo.initialize()
    monkeypatch.setattr(module,'MAX_SNAPSHOT_BYTES',10)
    with pytest.raises(WorkflowRunValidationError,match='字节上限'):
        WorkflowRunService(settings,TaskService(settings,None),repo).create_run(graph('abundance_input','matrix_summary','matrix'),input_bindings={'source':'matrix'})
