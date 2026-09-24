from pathlib import Path

import pytest

from backend.app.workflows.graph import WorkflowNode
from backend.app.workflows.state import (
    InvalidNodeTransition,
    WorkflowRunRepository,
)


def repository(tmp_path: Path) -> WorkflowRunRepository:
    repo = WorkflowRunRepository(tmp_path / "state" / "tasks.sqlite3")
    repo.initialize()
    return repo


def test_create_run_persists_ordered_nodes_and_compilation_identity(tmp_path: Path):
    repo = repository(tmp_path)

    created = repo.create_run(
        task_id="task-001",
        graph_hash="a" * 64,
        compiled_root=tmp_path / "workflow_runs" / "task-001" / "compiled",
        nodes=[
            WorkflowNode(id="qc", type="fastqc"),
            WorkflowNode(id="trim", type="fastp"),
        ],
        topological_order=("qc", "trim"),
    )

    assert created["task_id"] == "task-001"
    assert created["graph_hash"] == "a" * 64
    assert created["status"] == "queued"
    assert [node["node_id"] for node in created["nodes"]] == ["qc", "trim"]
    assert [node["status"] for node in created["nodes"]] == ["pending", "pending"]
    assert created["artifacts"] == []


def test_node_transitions_update_run_status_and_progress(tmp_path: Path):
    repo = repository(tmp_path)
    repo.create_run(
        task_id="task-001",
        graph_hash="a" * 64,
        compiled_root=tmp_path / "compiled",
        nodes=[WorkflowNode(id="qc", type="fastqc")],
        topological_order=("qc",),
    )

    repo.update_node_status("task-001", "qc", "queued")
    repo.update_node_status("task-001", "qc", "running", progress=12.5)
    running = repo.get_run("task-001")
    assert running is not None
    assert running["status"] == "running"
    assert running["started_at"] is not None
    assert running["nodes"][0]["progress"] == 12.5

    repo.update_node_status("task-001", "qc", "succeeded")
    succeeded = repo.get_run("task-001")
    assert succeeded is not None
    assert succeeded["status"] == "succeeded"
    assert succeeded["finished_at"] is not None
    assert succeeded["nodes"][0]["progress"] == 100.0


def test_illegal_transition_and_invalid_progress_do_not_mutate_state(tmp_path: Path):
    repo = repository(tmp_path)
    repo.create_run(
        task_id="task-001",
        graph_hash="a" * 64,
        compiled_root=tmp_path / "compiled",
        nodes=[WorkflowNode(id="qc", type="fastqc")],
        topological_order=("qc",),
    )

    with pytest.raises(InvalidNodeTransition, match="pending -> succeeded"):
        repo.update_node_status("task-001", "qc", "succeeded")
    with pytest.raises(ValueError, match="between 0 and 100"):
        repo.update_node_status("task-001", "qc", "queued", progress=101)

    unchanged = repo.get_run("task-001")
    assert unchanged is not None
    assert unchanged["status"] == "queued"
    assert unchanged["nodes"][0]["status"] == "pending"


def test_failed_node_can_be_requeued_for_guarded_retry(tmp_path: Path):
    repo = repository(tmp_path)
    repo.create_run(
        task_id="task-001",
        graph_hash="a" * 64,
        compiled_root=tmp_path / "compiled",
        nodes=[WorkflowNode(id="qc", type="fastqc")],
        topological_order=("qc",),
    )
    repo.update_node_status("task-001", "qc", "queued")
    repo.update_node_status("task-001", "qc", "running")
    repo.update_node_status(
        "task-001", "qc", "failed", error_message="tool exited with code 1"
    )

    failed = repo.get_run("task-001")
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["error_message"] == "tool exited with code 1"

    repo.update_node_status("task-001", "qc", "queued")
    retried = repo.get_run("task-001")
    assert retried is not None
    assert retried["status"] == "queued"
    assert retried["finished_at"] is None
    assert retried["error_message"] is None
    assert retried["nodes"][0]["error_message"] is None


def test_record_artifact_keeps_node_port_and_content_identity(tmp_path: Path):
    repo = repository(tmp_path)
    repo.create_run(
        task_id="task-001",
        graph_hash="a" * 64,
        compiled_root=tmp_path / "compiled",
        nodes=[WorkflowNode(id="qc", type="fastqc")],
        topological_order=("qc",),
    )
    result_path = (tmp_path / "outputs" / "fastqc.zip").resolve()

    artifact = repo.record_artifact(
        task_id="task-001",
        node_id="qc",
        port_id="reports",
        path=result_path,
        sha256="b" * 64,
        size_bytes=123,
    )

    assert artifact["node_id"] == "qc"
    assert artifact["port_id"] == "reports"
    assert artifact["path"] == str(result_path)
    loaded = repo.get_run("task-001")
    assert loaded is not None
    assert loaded["artifacts"] == [artifact]


def test_record_artifact_rejects_unknown_node_and_invalid_digest(tmp_path: Path):
    repo = repository(tmp_path)
    repo.create_run(
        task_id="task-001",
        graph_hash="a" * 64,
        compiled_root=tmp_path / "compiled",
        nodes=[WorkflowNode(id="qc", type="fastqc")],
        topological_order=("qc",),
    )

    with pytest.raises(KeyError):
        repo.record_artifact(
            task_id="task-001",
            node_id="missing",
            port_id="reports",
            path=(tmp_path / "result.zip").resolve(),
            sha256="b" * 64,
            size_bytes=1,
        )
    with pytest.raises(ValueError, match="SHA-256"):
        repo.record_artifact(
            task_id="task-001",
            node_id="qc",
            port_id="reports",
            path=(tmp_path / "result.zip").resolve(),
            sha256="not-a-digest",
            size_bytes=1,
        )
