from pathlib import Path
from backend.app.workflows.registry import default_node_registry
from backend.app.workflows.graph import WorkflowGraph
from backend.app.workflows.compiler import compile_workflow


def test_five_typed_starters_compile_with_registered_ports_and_defaults():
    from backend.app.services.typed_workflow_templates import typed_template_records
    registry=default_node_registry()
    records=typed_template_records(registry)
    assert len(records)==5
    for record in records:
        graph=WorkflowGraph.model_validate(record['workflow'])
        assert not any(n.type=='report' for n in graph.nodes)
        bindings={}
        for node in graph.nodes:
            definition=registry[node.type]
            assert node.parameters=={k:v['default'] for k,v in definition.parameters_schema.items() if 'default' in v}
            if definition.category=='input':
                kind={'contigs_input':'contigs','mag_input':'mags','abundance_input':'matrix'}.get(node.type,'reads')
                bindings[node.id]={'dataset_id':'fixture_'+node.id,'source_root':'fixture',
                    'contract':{'kind':kind,'data_type':definition.outputs[0].data_type,'files':[],
                        'samples':[],'cohort_id':'cohort'}}
        compiled=compile_workflow(graph,registry,project_root=Path(__file__).resolve().parents[2],input_contracts=bindings)
        assert set(compiled.node_aliases)=={n.id for n in graph.nodes}
        assert record['input_requirements'] and record['expected_outputs']
