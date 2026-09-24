import json
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.models import TaskRepository
from backend.app.services.tasks import TaskService
from backend.app.services.workflow_runs import (
    DynamicWorkflowRunner,
    WorkflowRunService,
    WorkflowRunValidationError,
)
from backend.app.workflows.graph import WorkflowEdge, WorkflowGraph, WorkflowNode
from backend.app.workflows.registry import default_node_registry
from backend.app.workflows.state import WorkflowRunRepository


def fastqc_graph() -> WorkflowGraph:
    return WorkflowGraph(
        schema_version="1.0",
        nodes=[
            WorkflowNode(id="input", type="fastq_input"),
            WorkflowNode(id="qc", type="fastqc"),
        ],
        edges=[
            WorkflowEdge(
                id="input-qc",
                source_node="input",
                source_port="reads",
                target_node="qc",
                target_port="reads",
            )
        ],
    )


def environment(tmp_path: Path, nextflow_bin: str = "nextflow"):
    input_root = tmp_path / "incoming"
    input_root.mkdir()
    manifest = input_root / "samples.csv"
    manifest.write_text(
        "sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n",
        encoding="utf-8",
    )
    settings = Settings(
        input_root=input_root,
        state_root=tmp_path / "runtime",
        workflow_path=tmp_path / "unused.nf",
        nextflow_bin=nextflow_bin,
        auto_run=False,
        poll_interval_seconds=0.01,
    )
    settings.prepare()
    task_repository = TaskRepository(settings.database_path)
    task_repository.initialize()
    task_service = TaskService(settings, task_repository)
    run_repository = WorkflowRunRepository(settings.database_path)
    run_repository.initialize()
    service = WorkflowRunService(
        settings,
        task_service,
        run_repository,
        registry=default_node_registry(),
    )
    return settings, manifest, run_repository, service


def test_create_run_compiles_materializes_and_persists_graph(tmp_path: Path):
    settings, manifest, repository, service = environment(tmp_path)

    run = service.create_run(fastqc_graph(), str(manifest))

    assert run["status"] == "queued"
    assert run["input_manifest"] == str(manifest.resolve())
    assert run["database_manifest"] is None
    assert [node["node_id"] for node in run["nodes"]] == ["input", "qc"]
    compiled_root = Path(run["compiled_root"])
    assert (compiled_root / "main.nf").is_file()
    assert (compiled_root / "nextflow.config").is_file()
    assert (compiled_root / "parameters.json").is_file()
    assert repository.get_run(run["task_id"])["graph_hash"] == run["graph_hash"]
    assert compiled_root.is_relative_to(settings.state_root.resolve())


def test_database_nodes_require_server_resolved_manifest(tmp_path: Path):
    _, manifest, _, service = environment(tmp_path)
    graph = WorkflowGraph(
        schema_version="1.0",
        nodes=[
            WorkflowNode(id="input", type="fastq_input"),
            WorkflowNode(id="taxonomy", type="taxonomy"),
        ],
        edges=[
            WorkflowEdge(
                id="input-taxonomy",
                source_node="input",
                source_port="reads",
                target_node="taxonomy",
                target_port="reads",
            )
        ],
        accepted_risks=[
            "risk:taxonomy:annotation_without_host_depletion"
        ],
    )

    with pytest.raises(WorkflowRunValidationError, match="database manifest"):
        service.create_run(graph, str(manifest))


def executable(tmp_path: Path, exit_code: int) -> Path:
    path = tmp_path / f"fake-nextflow-{exit_code}"
    path.write_text(f"#!/usr/bin/env bash\nexit {exit_code}\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def test_dynamic_runner_marks_all_nodes_succeeded_on_zero_exit(tmp_path: Path):
    fake = executable(tmp_path, 0)
    settings, manifest, repository, service = environment(tmp_path, str(fake))
    run = service.create_run(fastqc_graph(), str(manifest))
    runner = DynamicWorkflowRunner(settings, repository)

    runner.run(run["task_id"])

    completed = repository.get_run(run["task_id"])
    assert completed is not None
    assert completed["status"] == "succeeded"
    assert [node["status"] for node in completed["nodes"]] == [
        "succeeded",
        "succeeded",
    ]
    assert Path(completed["log_path"]).is_file()


def test_dynamic_runner_records_failure_without_shell_interpolation(tmp_path: Path):
    fake = executable(tmp_path, 42)
    settings, manifest, repository, service = environment(tmp_path, str(fake))
    run = service.create_run(fastqc_graph(), str(manifest))
    runner = DynamicWorkflowRunner(settings, repository)

    runner.run(run["task_id"])

    failed = repository.get_run(run["task_id"])
    assert failed is not None
    assert failed["status"] == "failed"
    assert any(node["status"] == "failed" for node in failed["nodes"])
    assert "42" in failed["error_message"]


def test_dynamic_runner_maps_nextflow_trace_to_editor_node(tmp_path: Path):
    settings, manifest, repository, service = environment(tmp_path)
    run = service.create_run(fastqc_graph(), str(manifest))
    runner = DynamicWorkflowRunner(settings, repository)
    runner._queue_nodes(run["task_id"])
    summary = json.loads(
        (Path(run["compiled_root"]) / "summary.json").read_text(encoding="utf-8")
    )
    alias = summary["node_aliases"]["qc"]
    trace = tmp_path / "trace.tsv"
    trace.write_text(
        "task_id\tname\tstatus\n"
        f"1\t{alias} (S01)\tRUNNING\n"
        f"2\t{alias} (S02)\tFAILED\n",
        encoding="utf-8",
    )
    log_path = tmp_path / "nextflow.log"
    log_path.touch()

    failed_node = runner._sync_trace(
        run["task_id"], trace, summary["node_aliases"], log_path
    )

    updated = repository.get_run(run["task_id"])
    qc = next(node for node in updated["nodes"] if node["node_id"] == "qc")
    assert failed_node == "qc"
    assert qc["status"] == "running"
    assert qc["progress"] == 5.0
