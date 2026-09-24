import json
import stat

import pytest

from backend.app.workflows.compiler import CompiledWorkflow
from backend.app.workflows.materializer import (
    CompilationArtifactConflict,
    materialize_compilation,
)


def compiled_workflow(graph_hash: str = "a" * 64) -> CompiledWorkflow:
    return CompiledWorkflow(
        source="nextflow.enable.dsl=2\nworkflow { }\n",
        nextflow_config="process { }\n",
        graph_hash=graph_hash,
        registry_version="1.0.0",
        compiler_version="1.0.0",
        topological_order=("input", "qc"),
        node_aliases={"qc": "WF_FASTQC_ABC"},
        node_parameters={"input": {}, "qc": {"threads": 4}},
        nextflow_parameters={"read_length": 150},
        node_output_channels={
            "input": {"reads": "node_input_reads"},
            "qc": {"report": "WF_FASTQC_ABC.out.reports"},
        },
    )


def test_materializer_atomically_writes_private_compilation_bundle(tmp_path):
    artifacts = materialize_compilation(
        compiled_workflow(),
        task_id="task-001",
        state_root=tmp_path,
    )

    expected_root = tmp_path / "workflow_runs" / "task-001" / "compiled"
    assert artifacts.root == expected_root
    assert artifacts.source_path.read_text(encoding="utf-8") == (
        "nextflow.enable.dsl=2\nworkflow { }\n"
    )
    assert artifacts.config_path.read_text(encoding="utf-8") == "process { }\n"
    assert json.loads(artifacts.parameters_path.read_text(encoding="utf-8")) == {
        "read_length": 150
    }
    summary = json.loads(artifacts.summary_path.read_text(encoding="utf-8"))
    assert summary == {
        "compiler_version": "1.0.0",
        "graph_hash": "a" * 64,
        "node_aliases": {"qc": "WF_FASTQC_ABC"},
        "node_output_channels": {
            "input": {"reads": "node_input_reads"},
            "qc": {"report": "WF_FASTQC_ABC.out.reports"},
        },
        "node_parameters": {"input": {}, "qc": {"threads": 4}},
        "registry_version": "1.0.0",
        "topological_order": ["input", "qc"],
    }
    assert stat.S_IMODE(artifacts.root.stat().st_mode) == 0o700
    for path in artifacts.files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(artifacts.root.parent.glob(".compiled-*")) == []


def test_materializer_rejects_unsafe_task_id_without_creating_run_directory(
    tmp_path,
):
    with pytest.raises(ValueError, match="task_id"):
        materialize_compilation(
            compiled_workflow(),
            task_id="task-001/../../outside",
            state_root=tmp_path,
        )

    assert not (tmp_path / "workflow_runs").exists()


def test_materializer_never_overwrites_existing_task_compilation(tmp_path):
    first = materialize_compilation(
        compiled_workflow(), task_id="task-001", state_root=tmp_path
    )
    original_source = first.source_path.read_bytes()

    with pytest.raises(CompilationArtifactConflict, match="already exists"):
        materialize_compilation(
            compiled_workflow(), task_id="task-001", state_root=tmp_path
        )

    assert first.source_path.read_bytes() == original_source


def test_materializer_snapshots_modules_and_scripts_for_execution(tmp_path):
    workflow_root = tmp_path / "source-workflow"
    (workflow_root / "modules").mkdir(parents=True)
    (workflow_root / "bin" / "core").mkdir(parents=True)
    (workflow_root / "modules" / "fastqc.nf").write_text(
        "process FASTQC_RAW {}\n", encoding="utf-8"
    )
    script = workflow_root / "bin" / "core" / "fastqc.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    script.chmod(0o700)

    artifacts = materialize_compilation(
        compiled_workflow(),
        task_id="task-001",
        state_root=tmp_path / "runtime",
        workflow_root=workflow_root,
    )

    assert (artifacts.root / "workflow/modules/fastqc.nf").is_file()
    bundled_script = artifacts.root / "bin/core/fastqc.sh"
    assert bundled_script.is_file()
    assert stat.S_IMODE(bundled_script.stat().st_mode) & stat.S_IXUSR
