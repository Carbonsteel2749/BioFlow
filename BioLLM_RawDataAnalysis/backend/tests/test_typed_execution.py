import json
from pathlib import Path
import pytest
import importlib.util
import sys
from backend.app.workflows.graph import WorkflowGraph
from backend.app.workflows.registry import default_node_registry
from backend.app.workflows.validation import validate_workflow_graph
from backend.tests.test_input_datasets import setup
from backend.app.workflows.compiler import compile_workflow, WorkflowCompilationError

PROJECT=Path(__file__).resolve().parents[2]


def graph(source='contigs_input', target='binning', port='assembly'):
    return WorkflowGraph.model_validate({'schema_version':'1.0','nodes':[{'id':'source','type':source},{'id':'sink','type':target}],
        'edges':[{'id':'edge','source_node':'source','source_port':port,'target_node':'sink','target_port':port}]})


def test_external_contigs_require_real_companion_reads():
    result=validate_workflow_graph(graph(),default_node_registry())
    assert 'external_contigs_require_reads' in {i.code for i in result.hard_errors}


def test_metadata_freeze_is_not_execution_verification(tmp_path):
    service,folder,spec=setup(tmp_path)
    service.register('abc123',spec)
    (folder/'contigs.fa').write_text('>c1\nTTTTN\n')
    snapshot=service.snapshot_metadata_only({'source':'abc123'},'task1')
    body=json.loads(snapshot.read_text())
    assert body['integrity_check']=='pending_execution_sha256'
    assert body['bindings']['source']['spec']==spec
    with pytest.raises(ValueError,match='校验'):service.verify_snapshot(snapshot)


def test_typed_compilation_rejects_wrong_or_missing_bindings_and_hashes_inputs(tmp_path):
    service,folder,spec=setup(tmp_path);record=service.register('abc123',spec)
    g=graph('mag_input','bin_annotation','mags')
    with pytest.raises(WorkflowCompilationError,match='binding'):
        compile_workflow(g,default_node_registry(),project_root=PROJECT,input_contracts={})
    with pytest.raises(WorkflowCompilationError,match='type'):
        compile_workflow(g,default_node_registry(),project_root=PROJECT,input_contracts={'source':record})


@pytest.mark.parametrize('stage',['clean','host_removed'])
def test_typed_reads_direct_annotation(tmp_path,stage):
    from backend.app.workflows.input_contracts import validate_input_contract
    for mate in (1,2):(tmp_path/f'r{mate}.fq').write_text(f'@r/{mate}\nACGT\n+\nIIII\n')
    spec={'kind':'reads','processing_status':stage,'processing_note':'external tools','confirmed':True,
          'samples':[{'sample_id':'s1','read1':'r1.fq','read2':'r2.fq'}]}
    record={'dataset_id':'reads1','spec':spec,'contract':validate_input_contract(spec,tmp_path),'source_root':str(tmp_path)}
    g=graph(stage+'_reads_input' if stage=='clean' else 'host_removed_reads_input','taxonomy','reads')
    g.accepted_risks=['risk:sink:annotation_without_host_depletion']
    compiled=compile_workflow(g,default_node_registry(),project_root=PROJECT,input_contracts={'source':record})
    assert set(compiled.node_aliases)=={'source','sink'}
    assert compiled.node_output_channels['source']['reads']
    changed={**record,'contract':{**record['contract'],'manifest_sha256':'a'*64}}
    second=compile_workflow(g,default_node_registry(),project_root=PROJECT,input_contracts={'source':changed})
    assert compiled.graph_hash!=second.graph_hash
    assert compiled.node_fingerprints['sink']!=second.node_fingerprints['sink']


def adapter(monkeypatch):
    import backend.app.workflows.input_contracts as contracts
    monkeypatch.setitem(sys.modules,'_input_contracts',contracts)
    spec=importlib.util.spec_from_file_location('typed_adapter',PROJECT/'workflow/bin/core/input_datasets.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_adapter_materializes_real_contigs_and_rejects_changed_bytes(tmp_path,monkeypatch):
    service,folder,spec=setup(tmp_path);service.register('abc123',spec)
    snap=service.snapshot({'source':'abc123'},'task1')
    module=adapter(monkeypatch)
    module.materialize(snap,'source',[folder/'contigs.fa'],tmp_path/'result')
    assert (tmp_path/'result/contigs.fasta').read_text()=='>c1\nACGTN\n'
    assert json.loads((tmp_path/'result/input.provenance.json').read_text())['trust']=='external_unverified'
    (folder/'contigs.fa').write_text('>c1\nTTTTN\n')
    with pytest.raises(ValueError):module.materialize(snap,'source',[folder/'contigs.fa'],tmp_path/'altered')


def test_matrix_summary_preserves_values_units_and_missingness(tmp_path,monkeypatch):
    from backend.app.workflows.input_contracts import validate_input_contract
    source=tmp_path/'dataset';source.mkdir()
    (source/'values.tsv').write_text('sample_id\tfeature_id\tvalue\ns1\tK1\t4\ns1\tK2\t0\ns2\tK1\t2\n')
    spec={'kind':'matrix','table':'values.tsv','feature_type':'KO','unit':'counts','normalization':'none'}
    contract=validate_input_contract(spec,source)
    (source/'input.provenance.json').write_text(json.dumps(contract))
    module=adapter(monkeypatch);module.matrix_summary(source,tmp_path/'summary')
    import csv
    with (tmp_path/'summary/sample_summary.tsv').open() as f:rows=list(csv.DictReader(f,delimiter='\t'))
    assert rows[0]['sum']=='4' and rows[0]['observed_features']=='2'
    assert rows[1]['sum']=='2' and rows[1]['observed_features']=='1'
    provenance=json.loads((tmp_path/'summary/summary.provenance.json').read_text())
    assert provenance['unit']=='counts' and provenance['normalization']=='none'
    assert provenance['transformation']=='none'
    assert provenance['producer']=='external_matrix_descriptive_summary'
    assert (tmp_path/'summary/feature_means.png').is_file()


def test_run_freezes_typed_binding_and_runner_checks_sha_before_launch(tmp_path,monkeypatch):
    from backend.app.config import Settings
    from backend.app.services.input_datasets import InputDatasetService
    from backend.app.services.workflow_runs import WorkflowRunService,DynamicWorkflowRunner
    from backend.app.services.tasks import TaskService
    from backend.app.workflows.state import WorkflowRunRepository
    from backend.app.workflows import materializer
    if sys.platform=='win32':monkeypatch.setattr(materializer,'_fsync_directory',lambda path:None)
    settings=Settings(input_root=tmp_path/'incoming',state_root=tmp_path/'state',workflow_path=PROJECT/'workflow/main.nf')
    settings.prepare();service=InputDatasetService(settings)
    folder=service.root/'matrix1';folder.mkdir(parents=True)
    (folder/'values.tsv').write_text('sample_id\tfeature_id\tvalue\ns1\tK1\t4\n')
    service.register('matrix1',{'kind':'matrix','table':'values.tsv','feature_type':'KO','unit':'counts','normalization':'none'})
    repo=WorkflowRunRepository(settings.database_path);repo.initialize()
    runs=WorkflowRunService(settings,TaskService(settings,None),repo)
    with monkeypatch.context() as admission:
        def forbidden_scan(*args,**kwargs):raise AssertionError('HTTP admission must not perform a full file scan')
        admission.setattr(InputDatasetService,'get',forbidden_scan)
        result=runs.create_run(graph('abundance_input','matrix_summary','matrix'),input_bindings={'source':'matrix1'})
    assert result['input_bindings']['source']['contract']['unit']=='counts'
    frozen=Path(result['compiled_root'])/'bin/core/_input_contracts.py'
    assert frozen.read_bytes()==(PROJECT/'backend/app/workflows/input_contracts.py').read_bytes()
    import os
    prior=(folder/'values.tsv').stat()
    (folder/'values.tsv').write_text('sample_id\tfeature_id\tvalue\ns1\tK1\t5\n')
    os.utime(folder/'values.tsv',ns=(prior.st_atime_ns,prior.st_mtime_ns))
    with pytest.raises(ValueError,match='校验'):DynamicWorkflowRunner(settings,repo).run(result['task_id'])
    assert not (settings.state_root/'logs'/result['task_id']/'attempt-001/nextflow.log').exists()
    repo.pause_run(result['task_id'],'test pause')
    resumed=runs.resume_run(result['task_id'])
    assert resumed['attempt']==2
    with pytest.raises(ValueError,match='校验'):DynamicWorkflowRunner(settings,repo).run(result['task_id'])


def test_reusable_diy_outputs_have_honest_types():
    from backend.app.services.dynamic_artifacts import _kind
    assert _kind(Path('s1_read1.fastq.gz'),'fastp')[0]=='reads.clean'
    assert _kind(Path('contigs.fasta'),'assembly')[0]=='assembly.contigs'
    assert _kind(Path('reassembled_bins/bin1.fa'),'bin_reassembly')[0]=='mag.fasta'
    assert _kind(Path('final_assembly.fasta'),'bin_reassembly') is None
    assert _kind(Path('canonical.tsv'),'matrix_summary')[0]=='matrix.canonical'


def test_legacy_compilation_still_accepts_assembly_binning_without_companion():
    g=WorkflowGraph.model_validate({'schema_version':'1.0','nodes':[{'id':'raw','type':'fastq_input'},
        {'id':'clean','type':'fastp'},{'id':'assembly','type':'assembly'},{'id':'bins','type':'binning'}],
        'edges':[{'id':str(i),'source_node':a,'source_port':ap,'target_node':b,'target_port':bp}
                 for i,(a,ap,b,bp) in enumerate([('raw','reads','clean','reads'),('clean','reads','assembly','reads'),('assembly','assembly','bins','assembly')])]})
    compiled=compile_workflow(g,default_node_registry(),project_root=PROJECT)
    assert compiled.node_output_channels['bins']['bins']
    g.edges.append(type(g.edges[0])(id='extra',source_node='clean',source_port='reads',target_node='bins',target_port='reads'))
    result=validate_workflow_graph(g,default_node_registry())
    assert 'redundant_binning_reads' in {i.code for i in result.hard_errors}


def test_connected_reads_manifest_uses_actual_files(tmp_path,monkeypatch):
    module=adapter(monkeypatch)
    reads=[]
    for name in ('one.fq','two.fq'):
        path=tmp_path/name;path.write_text('@r\nAC\n+\nII\n');reads.append(str(path))
    module.reads_manifest(['sampleA'],reads,tmp_path/'samples.csv')
    import csv
    with (tmp_path/'samples.csv').open() as handle:rows=list(csv.DictReader(handle))
    assert rows==[{'sample_id':'sampleA','read1':str(Path(reads[0]).resolve()),'read2':str(Path(reads[1]).resolve())}]
    with pytest.raises(ValueError):module.reads_manifest(['sampleA'],reads[:1],tmp_path/'bad.csv')


def test_typed_archive_entries_keep_original_source_and_contract(tmp_path):
    from backend.app.services.full_archive import typed_archive_entries
    service,folder,spec=setup(tmp_path);service.register('abc123',spec)
    snapshot=service.snapshot({'source':'abc123'},'task1')
    entries=typed_archive_entries(service.settings,'task1',snapshot)
    assert entries[0][1]=='execution/input-bindings.json'
    assert any(path==folder/'contigs.fa' and name=='raw/typed/source/contigs.fa' for path,name in entries)
    (folder/'contigs.fa').write_text('>c1\nTTTTN\n')
    with pytest.raises(ValueError):typed_archive_entries(service.settings,'task1',snapshot)


def test_matrix_preflight_checks_dependencies_without_database(tmp_path,monkeypatch):
    from types import SimpleNamespace
    if sys.platform=='win32':monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace())
    from backend.app.services import template_preflight
    from backend.app.config import Settings
    # Environment probes are external; graph/tool routing remains real.
    monkeypatch.setattr(template_preflight.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0))
    settings=Settings(input_root=tmp_path/'incoming',state_root=tmp_path/'state',workflow_path=PROJECT/'workflow/main.nf')
    result=template_preflight.preflight_workflow(graph('abundance_input','matrix_summary','matrix'),default_node_registry(),settings)
    assert result['ready']
    assert any(item['name']=='matrix_python_dependencies' for item in result['checks'])
    assert not any('database' in item['name'] for item in result['checks'])


def test_read_adapter_preserves_existing_assembly_mate_convention(tmp_path,monkeypatch):
    from backend.app.services.input_datasets import InputDatasetService
    from types import SimpleNamespace
    service=InputDatasetService(SimpleNamespace(input_root=tmp_path/'input',state_root=tmp_path/'state'))
    folder=service.root/'reads';folder.mkdir(parents=True)
    for mate in (1,2):(folder/f'r{mate}.fq').write_text(f'@r/{mate}\nACGT\n+\nIIII\n')
    spec={'kind':'reads','processing_status':'clean','processing_note':'external','confirmed':True,
        'samples':[{'sample_id':'s1','read1':'r1.fq','read2':'r2.fq'}]}
    service.register('reads',spec);snapshot=service.snapshot({'reads':'reads'},'task1')
    adapter(monkeypatch).materialize(snapshot,'reads',[folder/'r1.fq',folder/'r2.fq'],tmp_path/'out')
    assert (tmp_path/'out/s1.R1.input.fastq').read_text()=='@r/1\nACGT\n+\nIIII\n'
    assert (tmp_path/'out/s1.R2.input.fastq').is_file()
