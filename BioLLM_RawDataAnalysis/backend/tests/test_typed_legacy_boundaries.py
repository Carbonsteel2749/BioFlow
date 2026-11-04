import json
import sys
from backend.app.services.artifacts import ARTIFACT_TYPES
from backend.app.workflows import materializer
from backend.app.workflows.compiler import compile_workflow
from backend.app.workflows.registry import default_node_registry
from backend.tests.test_typed_execution import graph,PROJECT


def test_artifact_json_schema_matches_runtime_catalog():
    schema=json.loads((PROJECT/'workflow/assets/artifact.schema.json').read_text())
    assert set(schema['$defs']['artifact']['properties']['artifact_type']['enum'])==set(ARTIFACT_TYPES)


def test_empty_optional_bin_directory_still_materializes(tmp_path,monkeypatch):
    if sys.platform=='win32':monkeypatch.setattr(materializer,'_fsync_directory',lambda p:None)
    workflow=tmp_path/'workflow';(workflow/'bin').mkdir(parents=True)
    (workflow/'modules').mkdir()
    compiled=compile_workflow(graph('fastq_input','fastp','reads'),default_node_registry(),project_root=PROJECT)
    result=materializer.materialize_compilation(compiled,task_id='legacy',state_root=tmp_path/'state',workflow_root=workflow)
    assert (result.root/'bin/core/_input_contracts.py').is_file()
