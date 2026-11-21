import csv
import json
import os
from pathlib import Path
import pytest

from backend.app.config import Settings
from backend.app.models import TaskRepository
from backend.app.artifacts import ArtifactRepository
from backend.app.workflows.state import WorkflowRunRepository
from backend.app.workflows.graph import WorkflowNode
from backend.app.services.input_datasets import InputDatasetService
from backend.app.services.datasets import DatasetService
from backend.app.services.dataset_cleanup import DatasetCleanup


def environment(tmp_path):
    # A supported layout with input_root == state_root makes the discovery bug
    # visible as a phantom CSV dataset rather than merely wasted discovery work.
    settings=Settings(input_root=tmp_path/'state',state_root=tmp_path/'state',workflow_path=tmp_path/'main.nf')
    settings.prepare();repo=TaskRepository(settings.database_path);repo.initialize()
    workflows=WorkflowRunRepository(settings.database_path);workflows.initialize()
    artifacts=ArtifactRepository(settings.database_path);artifacts.initialize()
    catalog=DatasetService(settings,repo);catalog.initialize()
    raw=settings.input_root/'legacy';raw.mkdir()
    for mate in (1,2):(raw/f'r{mate}.fq').write_text(f'@r/{mate}\nACGT\n+\nIIII\n')
    manifest=raw/'samples.csv'
    with manifest.open('w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['sample_id','read1','read2']);writer.writerow(['s1',str(raw/'r1.fq'),str(raw/'r2.fq')])
    legacy_id=catalog.register_manifest(manifest,source='uploaded')
    inputs=InputDatasetService(settings);folder=inputs.root/'external';folder.mkdir(parents=True)
    for mate in (1,2):(folder/f'r{mate}.fq').write_bytes((raw/f'r{mate}.fq').read_bytes())
    spec={'kind':'reads','processing_status':'clean','processing_note':'external','confirmed':True,
          'samples':[{'sample_id':'private_sample','read1':'r1.fq','read2':'r2.fq'}]}
    inputs.register('external',spec);snapshot=inputs.snapshot({'source':'external'},'typed1')
    workflows.create_run(task_id='typed1',graph_hash='a'*64,compiled_root=settings.state_root/'workflow_runs/typed1/compiled',
        nodes=[WorkflowNode(id='source',type='clean_reads_input')],topological_order=['source'],input_manifest=snapshot,database_manifest=None)
    return settings,repo,catalog,legacy_id,manifest,raw,folder,snapshot


def test_legacy_catalog_never_discovers_typed_binding_json(tmp_path):
    settings,repo,catalog,legacy_id,*_=environment(tmp_path)
    assert [item['id'] for item in catalog.list()['items']]==[legacy_id]
    catalog.initialize()
    assert catalog.list()['total']==1


def test_unrelated_typed_inputs_do_not_disable_legacy_reference_cleanup(tmp_path):
    settings,repo,catalog,legacy_id,manifest,raw,folder,snapshot=environment(tmp_path)
    cleanup=DatasetCleanup(settings,repo);files=[manifest,raw/'r1.fq',raw/'r2.fq']
    with repo._connect() as db:
        dataset=db.execute('SELECT * FROM datasets WHERE id=?',(legacy_id,)).fetchone()
        cleanup._check_references(db,dataset,files,None)
        # Immutable metadata still describes these bytes, but a hardlink must
        # protect the legacy source even if the stored path differs.
        (folder/'r1.fq').unlink();os.link(raw/'r1.fq',folder/'r1.fq')
        with pytest.raises(ValueError,match='引用'):cleanup._check_references(db,dataset,files,None)
    assert all(path.exists() for path in files)


def test_typed_snapshot_tampering_stays_fail_closed_for_cleanup(tmp_path):
    settings,repo,catalog,legacy_id,manifest,raw,folder,snapshot=environment(tmp_path)
    snapshot.write_text('{}')
    with repo._connect() as db:
        dataset=db.execute('SELECT * FROM datasets WHERE id=?',(legacy_id,)).fetchone()
        with pytest.raises(ValueError):DatasetCleanup(settings,repo)._check_references(db,dataset,[manifest,raw/'r1.fq',raw/'r2.fq'],None)


@pytest.mark.parametrize('artifact_type',['assembly.contigs','matrix.canonical','matrix.descriptive_summary','figure.matrix'])
def test_new_matrix_artifacts_are_registerable(tmp_path,artifact_type):
    from backend.app.services.artifacts import ArtifactService
    settings,repo,*_=environment(tmp_path)
    artifacts=ArtifactRepository(settings.database_path);service=ArtifactService(artifacts,settings.state_root/'outputs')
    output=settings.state_root/'outputs/typed1/canonical.tsv';output.parent.mkdir(parents=True);output.write_text('sample_id\tfeature_id\tvalue\n')
    result=service.register(task_id='typed1',producer='dynamic.matrix_summary',artifact_type=artifact_type,path=output,media_type='text/tab-separated-values')
    assert result['artifact_type']==artifact_type
    assert result['source_file']=='canonical.tsv'
    assert service.get(result['artifact_id'])['source_file']=='canonical.tsv'
    assert service.list_for_task('typed1')[0]['source_file']=='canonical.tsv'


def test_task_cleanup_protects_typed_originals_without_blocking_unrelated_cache(tmp_path):
    from backend.app.services.task_cleanup import TaskCleanupService,CleanupConflict
    settings,repo,catalog,legacy_id,manifest,raw,folder,snapshot=environment(tmp_path)
    cleanup=TaskCleanupService(settings,repo,None)
    with repo._connect() as db:
        cleanup._references(db,'unrelated',[settings.state_root/'work/unrelated'],'cache')
        with pytest.raises(CleanupConflict):cleanup._references(db,'unrelated',[folder],'cache')


def test_typed_diagnostic_redaction_uses_frozen_sample_ids(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    if sys.platform=='win32':monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace())
    from backend.app.services.dynamic_repairs import DynamicRepairService
    settings,repo,catalog,legacy_id,manifest,raw,folder,snapshot=environment(tmp_path)
    service=DynamicRepairService(settings,None)
    text=service._redact('failure for private_sample at '+str(folder/'r1.fq'),{'task_id':'typed1','input_manifest':str(snapshot)})
    assert 'private_sample' not in text
    assert str(folder) not in text


def test_typed_artifact_publication_keeps_sample_scope(tmp_path):
    from backend.app.services.artifacts import ArtifactService
    from backend.app.services.dynamic_artifacts import publish_dynamic_artifacts
    settings,repo,catalog,legacy_id,manifest,raw,folder,snapshot=environment(tmp_path)
    workflows=WorkflowRunRepository(settings.database_path)
    with repo._connect() as db:db.execute("UPDATE workflow_node_runs SET node_type='taxonomy' WHERE task_id='typed1'")
    compiled=settings.state_root/'workflow_runs/typed1/compiled';compiled.mkdir()
    (compiled/'summary.json').write_text(json.dumps({'node_output_roots':{'source':'nodes/source'}}))
    output=settings.state_root/'outputs/typed1/nodes/source/taxonomy/private_sample/species_abundance.tsv'
    output.parent.mkdir(parents=True);output.write_text('sample_id\tfeature_id\tvalue\nprivate_sample\tA\t1\n')
    for state in ('queued','running','succeeded'):workflows.update_node_status('typed1','source',state)
    service=ArtifactService(ArtifactRepository(settings.database_path),settings.state_root/'outputs')
    records=publish_dynamic_artifacts(settings,workflows,service,'typed1')
    assert len(records)==1 and records[0]['sample_scope']=='sample'
    assert records[0]['sample_id']=='private_sample'
