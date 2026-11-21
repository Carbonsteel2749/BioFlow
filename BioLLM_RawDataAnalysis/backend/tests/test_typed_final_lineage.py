import copy
import json
from pathlib import Path
import pytest
from backend.app.workflows.compiler import compile_workflow
from backend.app.workflows.graph import WorkflowGraph, WorkflowNode
from backend.app.workflows.registry import default_node_registry
from backend.app.workflows.materializer import _summary
from backend.app.services.typed_workflow_templates import typed_template_records

PROJECT = Path(__file__).resolve().parents[2]


def branched(reassembly=False):
    registry=default_node_registry()
    base=next(r['workflow'] for r in typed_template_records(registry) if r['template_id']=='builtin-typed-contigs')
    nodes=[];edges=[];bindings={}
    for prefix,cohort in [('a','cohort_A'),('b','cohort_B')]:
        part=copy.deepcopy(base)
        for n in part['nodes']:
            n['id']=prefix+n['id'];nodes.append(n)
            if registry[n['type']].category=='input':
                bindings[n['id']]={'dataset_id':n['id'],'source_root':'fixture','contract':{
                    'kind':'contigs' if n['type']=='contigs_input' else 'reads',
                    'data_type':registry[n['type']].outputs[0].data_type,'cohort_id':cohort,'files':[],'samples':[]}}
        for e in part['edges']:
            e.update(id=prefix+e['id'],source_node=prefix+e['source_node'],target_node=prefix+e['target_node']);edges.append(e)
        previous=prefix+'refinement'
        if reassembly:
            nodes.append({'id':prefix+'reassembly','type':'bin_reassembly'})
            edges.append(dict(id=prefix+'re',source_node=previous,source_port='bins',target_node=prefix+'reassembly',target_port='bins'))
            previous=prefix+'reassembly'
        nodes.append({'id':prefix+'annotation','type':'bin_annotation'})
        edges.append(dict(id=prefix+'ann',source_node=previous,source_port='mags' if reassembly else 'bins',target_node=prefix+'annotation',target_port='mags'))
    nodes += [{'id':'direct','type':'mag_input'},{'id':'directann','type':'bin_annotation'}]
    edges.append(dict(id='directedge',source_node='direct',source_port='mags',target_node='directann',target_port='mags'))
    bindings['direct']={'dataset_id':'direct','source_root':'fixture','contract':{'kind':'mags','data_type':'mag_set','cohort_id':'direct_C','files':[]}}
    bindings['direct']['contract']['data_type']=registry['mag_input'].outputs[0].data_type
    graph=WorkflowGraph.model_validate(dict(schema_version='1.0',nodes=nodes,edges=edges))
    return graph,bindings


@pytest.mark.parametrize('reassembly',[False,True])
def test_branch_cohorts_are_frozen_for_annotation_and_cache(reassembly,tmp_path):
    graph,bindings=branched(reassembly)
    compiled=compile_workflow(graph,default_node_registry(),project_root=PROJECT,input_contracts=bindings)
    frozen=_summary(compiled)['node_cohorts']
    assert frozen['aannotation']=='cohort_A'
    assert frozen['bannotation']=='cohort_B'
    assert frozen['directann']=='direct_C'
    assert "params.bioflow_node_cohorts['aannotation']" in compiled.source
    assert "params.bioflow_node_cohorts['bannotation']" in compiled.source
    assert compiled.nextflow_parameters['bioflow_node_cohorts']==frozen
    changed=copy.deepcopy(bindings);changed['acontigs']['contract']['cohort_id']='changed_A'
    other=compile_workflow(graph,default_node_registry(),project_root=PROJECT,input_contracts=changed)
    assert other.node_fingerprints['aannotation']!=compiled.node_fingerprints['aannotation']
    assert other.node_fingerprints['bannotation']==compiled.node_fingerprints['bannotation']
    from backend.tests.test_typed_compatibility import environment
    from backend.app.workflows.state import WorkflowRunRepository
    from backend.app.artifacts import ArtifactRepository
    from backend.app.services.artifacts import ArtifactService
    from backend.app.services.dynamic_artifacts import publish_dynamic_artifacts
    settings,*_=environment(tmp_path);repo=WorkflowRunRepository(settings.database_path)
    root=settings.state_root/'workflow_runs/branches/compiled';root.mkdir(parents=True)
    (root/'summary.json').write_text(json.dumps(_summary(compiled)))
    repo.create_run(task_id='branches',graph_hash=compiled.graph_hash,compiled_root=root,nodes=graph.nodes,
        topological_order=compiled.topological_order,input_manifest=settings.input_root/'legacy/samples.csv',database_manifest=None)
    for node,cohort in [('aannotation','cohort_A'),('bannotation','cohort_B'),('directann','direct_C')]:
        for status in ('queued','running','succeeded'):repo.update_node_status('branches',node,status)
        path=settings.state_root/'outputs/branches'/compiled.node_output_roots[node]/'mag_annotation.tsv'
        path.parent.mkdir(parents=True);path.write_text('cohort_id\n'+cohort+'\n')
    service=ArtifactService(ArtifactRepository(settings.database_path),settings.state_root/'outputs')
    records=publish_dynamic_artifacts(settings,repo,service,'branches')
    assert {r['node_id']:r['cohort_id'] for r in records}=={'aannotation':'cohort_A','bannotation':'cohort_B','directann':'direct_C'}
    assert all((service.output_root/'branches'/r['source_file']).read_text().splitlines()[1]==r['cohort_id'] for r in records)


def test_builtin_native_bins_and_quality_are_published_narrowly(tmp_path):
    from backend.tests.test_typed_compatibility import environment
    from backend.app.workflows.state import WorkflowRunRepository
    from backend.app.artifacts import ArtifactRepository
    from backend.app.services.artifacts import ArtifactService
    from backend.app.services.dynamic_artifacts import publish_dynamic_artifacts
    settings,*_=environment(tmp_path)
    registry=default_node_registry()
    graph=WorkflowGraph.model_validate(next(r['workflow'] for r in typed_template_records(registry) if r['template_id']=='builtin-typed-contigs'))
    bindings={n.id:{'dataset_id':n.id,'source_root':'fixture','contract':{'kind':'contigs' if n.type=='contigs_input' else 'reads',
        'data_type':registry[n.type].outputs[0].data_type,'cohort_id':'declared_cohort','files':[],'samples':[]}}
        for n in graph.nodes if registry[n.type].category=='input'}
    compiled=compile_workflow(graph,registry,project_root=PROJECT,input_contracts=bindings)
    repo=WorkflowRunRepository(settings.database_path)
    root=settings.state_root/'workflow_runs/native/compiled';root.mkdir(parents=True)
    (root/'summary.json').write_text(json.dumps(_summary(compiled)))
    repo.create_run(task_id='native',graph_hash=compiled.graph_hash,compiled_root=root,nodes=graph.nodes,
        topological_order=compiled.topological_order,input_manifest=settings.input_root/'legacy/samples.csv',database_manifest=None)
    for node in graph.nodes:
        for status in ('queued','running','succeeded'):repo.update_node_status('native',node.id,status)
    out=settings.state_root/'outputs/native'
    files={
        'binning':['mag/binning/metabat2_bins/bin1.fa','mag/binning/maxbin2_bins/bin2.fa','mag/binning/concoct_bins/bin3.fa'],
        'refinement':['mag/bin_refinement/metawrap_70_5_bins/MAG1.fa','mag/bin_refinement/metawrap_70_5_bins.stats']}
    for node,paths in files.items():
        for relative in paths+['private/secret.fa','logs/other.fa']:
            path=out/compiled.node_output_roots[node]/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('quality evidence' if path.suffix=='.stats' else '>c\nACGT\n')
    service=ArtifactService(ArtifactRepository(settings.database_path),settings.state_root/'outputs')
    records=publish_dynamic_artifacts(settings,repo,service,'native')
    assert len(records)==5
    assert {r['artifact_type'] for r in records if r['node_id']=='binning'}=={'mag.candidate_fasta'}
    refined=next(r for r in records if r['artifact_type']=='mag.fasta')
    assert refined['cohort_id']=='declared_cohort'
    assert refined['metadata']['quality_status']=='threshold_filtered_unverified'
    assert len(refined['sha256'])==64 and refined['source_file'].endswith('/MAG1.fa')
    quality=next(r for r in records if r['artifact_type']=='mag.quality_report')
    assert (out/quality['source_file']).read_text()=='quality evidence'
    assert all(r['cohort_id']=='declared_cohort' for r in records)
