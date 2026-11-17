import json
import pytest
from pathlib import Path
from types import SimpleNamespace
from backend.app.services.full_archive import report_package_not_requested


@pytest.mark.parametrize('raw', [False, True])
def test_only_validated_typed_workflow_omits_legacy_report_requirement(tmp_path, raw):
    settings=SimpleNamespace(state_root=tmp_path)
    compiled=tmp_path/'workflow_runs/task1/compiled';compiled.mkdir(parents=True)
    manifest=tmp_path/'workflow_runs/task1/inputs/bindings.json'
    graph={'schema_version':'1.0','nodes':[{'id':'input','type':'abundance_input'},{'id':'summary','type':'matrix_summary'}],
        'edges':[{'id':'e1','source_node':'input','source_port':'matrix','target_node':'summary','target_port':'matrix'}]}
    if raw:
        graph['nodes']=[{'id':'input','type':'fastq_input'},{'id':'summary','type':'fastqc'}]
        graph['edges'][0].update(source_port='reads',target_port='reads')
    (compiled/'workflow.json').write_text(json.dumps(graph))
    task={'compiled_root':str(compiled)}
    assert report_package_not_requested(settings,'task1','workflow',task,manifest)
    assert not report_package_not_requested(settings,'task1','standard',task,manifest)
    assert not report_package_not_requested(settings,'task1','workflow',task,Path('samples.csv'))
    graph['nodes'].append({'id':'report','type':'report'})
    (compiled/'workflow.json').write_text(json.dumps(graph))
    assert not report_package_not_requested(settings,'task1','workflow',task,manifest)
    (compiled/'workflow.json').write_text('{}')
    assert not report_package_not_requested(settings,'task1','workflow',task,manifest)
