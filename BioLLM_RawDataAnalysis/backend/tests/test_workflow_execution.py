from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.workflows.compiler import CompiledWorkflow
from backend.app.workflows.execution import build_nextflow_command
from backend.app.workflows.materializer import materialize_compilation


def compiled_workflow() -> CompiledWorkflow:
    return CompiledWorkflow(
        source="nextflow.enable.dsl=2\nworkflow { }\n",
        nextflow_config="process.executor = 'local'\n",
        graph_hash="a" * 64,
        registry_version="1.0",
        compiler_version="1.0",
        topological_order=("input", "qc"),
        node_aliases={"qc": "WF_FASTQC_TEST"},
        node_parameters={"input": {}, "qc": {"threads": 4}},
        nextflow_parameters={"read_length": 150},
        node_output_channels={
            "input": {"reads": "node_input_reads"},
            "qc": {"report": "WF_FASTQC_TEST.out.reports"},
        },
    )


def settings(tmp_path: Path) -> Settings:
    return Settings(
        input_root=tmp_path / "incoming",
        state_root=tmp_path / "runtime",
        workflow_path=tmp_path / "workflow" / "main.nf",
        nextflow_bin="/opt/nextflow/bin/nextflow",
    )


def create_inputs(tmp_path: Path):
    artifacts = materialize_compilation(
        compiled_workflow(), task_id="task-001", state_root=tmp_path / "runtime"
    )
    manifest_dir = tmp_path / "incoming files;safe"
    manifest_dir.mkdir()
    manifest = manifest_dir / "manifest.csv"
    manifest.write_text("sample_id,read1,read2\n", encoding="utf-8")
    database_manifest = tmp_path / "database manifest.json"
    database_manifest.write_text("{}\n", encoding="utf-8")
    return artifacts, manifest, database_manifest


def option_value(command: tuple[str, ...], option: str) -> str:
    return command[command.index(option) + 1]


def test_build_command_returns_argument_vector_with_private_run_paths(tmp_path: Path):
    artifacts, manifest, database_manifest = create_inputs(tmp_path)

    command = build_nextflow_command(
        settings(tmp_path),
        task_id="task-001",
        artifacts=artifacts,
        input_manifest=manifest,
        database_manifest=database_manifest,
        resume=True,
    )

    assert isinstance(command, tuple)
    assert command[:7] == (
        "/opt/nextflow/bin/nextflow",
        "-c",
        str(artifacts.config_path),
        "run",
        str(artifacts.source_path),
        "-ansi-log",
        "false",
    )
    assert option_value(command, "-params-file") == str(artifacts.parameters_path)
    assert option_value(command, "--input_manifest") == str(manifest.resolve())
    assert option_value(command, "--database_manifest") == str(
        database_manifest.resolve()
    )
    assert option_value(command, "--outdir") == str(
        (tmp_path / "runtime" / "outputs" / "task-001").resolve()
    )
    assert option_value(command, "-work-dir") == str(
        (tmp_path / "runtime" / "work" / "task-001").resolve()
    )
    assert option_value(command, "-with-trace") == str(
        (tmp_path / "runtime" / "logs" / "task-001" / "trace.tsv").resolve()
    )
    assert command[-1] == "-resume"
    assert str(manifest.resolve()) in command
    assert "files;safe" not in command


def test_database_manifest_is_optional_for_non_database_graphs(tmp_path: Path):
    artifacts, manifest, _ = create_inputs(tmp_path)

    command = build_nextflow_command(
        settings(tmp_path),
        task_id="task-001",
        artifacts=artifacts,
        input_manifest=manifest,
    )

    assert "--database_manifest" not in command
    assert "-resume" not in command


def test_rejects_unsafe_task_id_before_constructing_paths(tmp_path: Path):
    artifacts, manifest, _ = create_inputs(tmp_path)

    with pytest.raises(ValueError, match="task_id"):
        build_nextflow_command(
            settings(tmp_path),
            task_id="task-001; touch /tmp/pwned",
            artifacts=artifacts,
            input_manifest=manifest,
        )


def test_rejects_missing_input_or_compilation_file(tmp_path: Path):
    artifacts, manifest, _ = create_inputs(tmp_path)
    artifacts.source_path.unlink()

    with pytest.raises(FileNotFoundError, match="compiled source"):
        build_nextflow_command(
            settings(tmp_path),
            task_id="task-001",
            artifacts=artifacts,
            input_manifest=manifest,
        )

    with pytest.raises(FileNotFoundError, match="input manifest"):
        build_nextflow_command(
            settings(tmp_path),
            task_id="task-001",
            artifacts=materialize_compilation(
                compiled_workflow(),
                task_id="task-002",
                state_root=tmp_path / "runtime",
            ),
            input_manifest=tmp_path / "missing.csv",
        )


def test_rejects_compilation_artifact_that_escapes_bundle_root(tmp_path: Path):
    artifacts, manifest, _ = create_inputs(tmp_path)
    outside = tmp_path / "outside.nf"
    outside.write_text("workflow {}\n", encoding="utf-8")
    artifacts.source_path.unlink()
    artifacts.source_path.symlink_to(outside)

    with pytest.raises(ValueError, match="outside compilation root"):
        build_nextflow_command(
            settings(tmp_path),
            task_id="task-001",
            artifacts=artifacts,
            input_manifest=manifest,
        )
