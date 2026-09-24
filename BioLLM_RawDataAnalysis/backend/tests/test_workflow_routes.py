import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture
def workflow_client(tmp_path: Path):
    input_root = tmp_path / "incoming"
    state_root = tmp_path / "runtime"
    workflow_path = tmp_path / "workflow" / "main.nf"
    input_root.mkdir()
    workflow_path.parent.mkdir()
    workflow_path.write_text(
        "nextflow.enable.dsl=2\n",
        encoding="utf-8",
    )
    manifest = input_root / "samples.csv"
    manifest.write_text(
        "sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n",
        encoding="utf-8",
    )
    settings = Settings(
        input_root=input_root,
        state_root=state_root,
        workflow_path=workflow_path,
        auto_run=False,
    )
    with TestClient(create_app(settings)) as client:
        yield client, settings, manifest


def test_template_routes_copy_and_version_without_overwriting(
    workflow_client,
):
    client, _, _ = workflow_client
    listed = client.get("/api/workflows/templates")
    assert listed.status_code == 200
    assert len(listed.json()) == 2

    copied_response = client.post(
        "/api/workflows/templates/builtin-read-profile/copy",
        json={
            "name": "研究模板",
            "template_id": "study",
        },
    )
    assert copied_response.status_code == 201
    copied = copied_response.json()
    changed = copied["workflow"]
    changed["nodes"][2]["parameters"]["threads"] = 16

    updated_response = client.put(
        "/api/workflows/templates/study",
        json={
            "name": "研究模板",
            "base_version": 1,
            "workflow": changed,
        },
    )
    assert updated_response.status_code == 200
    assert updated_response.json()["version"] == 2
    assert (
        client.get(
            "/api/workflows/templates/study",
            params={"version": 1},
        ).json()["workflow"]["nodes"][2]["parameters"]["threads"]
        == 4
    )

    stale = client.put(
        "/api/workflows/templates/study",
        json={
            "name": "过期版本",
            "base_version": 1,
            "workflow": changed,
        },
    )
    assert stale.status_code == 409


def test_ai_unavailable_does_not_break_manual_editor_or_tasks(
    workflow_client,
):
    client, _, manifest = workflow_client

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "ollama offline",
            request=request,
        )

    client.app.state.workflow_ai_service._client = httpx.Client(
        transport=httpx.MockTransport(unavailable)
    )
    response = client.post(
        "/api/workflows/ai/proposals",
        json={
            "template_id": "builtin-read-profile",
            "instruction": "把 fastp 线程数改为 8",
        },
    )

    assert response.status_code == 503
    assert "manual workflow editing" in response.json()["detail"]
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/workflows/templates").status_code == 200
    task = client.post(
        "/api/tasks",
        json={"manifest_path": str(manifest)},
    )
    assert task.status_code == 201


def test_ai_route_requires_confirmation_before_new_version(
    workflow_client,
):
    client, _, _ = workflow_client
    copied = client.post(
        "/api/workflows/templates/builtin-read-profile/copy",
        json={
            "name": "研究模板",
            "template_id": "study-ai",
        },
    ).json()

    content = {
        "summary": "将 fastp 线程数调整为 8。",
        "changes": [
            {
                "operation": "update_parameters",
                "node_id": "trim",
                "parameters": {"threads": 8},
            }
        ],
    }
    client.app.state.workflow_ai_service._client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "message": {
                        "content": json.dumps(
                            content,
                            ensure_ascii=False,
                        )
                    }
                },
            )
        )
    )

    proposed_response = client.post(
        "/api/workflows/ai/proposals",
        json={
            "template_id": "study-ai",
            "version": 1,
            "instruction": "把 fastp 线程数改为 8",
        },
    )
    assert proposed_response.status_code == 201
    proposal = proposed_response.json()
    assert proposal["status"] == "pending_confirmation"
    assert (
        client.get(
            "/api/workflows/templates/study-ai"
        ).json()["version"]
        == 1
    )
    assert copied["workflow"]["nodes"][2]["parameters"]["threads"] == 4

    confirmed = client.post(
        "/api/workflows/ai/proposals/"
        + proposal["proposal_id"]
        + "/confirmation",
        json={"confirmed": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["proposal"]["status"] == "applied"
    assert confirmed.json()["template"]["version"] == 2
    latest = client.get(
        "/api/workflows/templates/study-ai"
    ).json()
    assert latest["version"] == 2
    assert latest["workflow"]["nodes"][2]["parameters"]["threads"] == 8

    duplicate = client.post(
        "/api/workflows/ai/proposals/"
        + proposal["proposal_id"]
        + "/confirmation",
        json={"confirmed": True},
    )
    assert duplicate.status_code == 409


def test_execution_snapshot_route_is_create_once_and_verifiable(
    workflow_client,
):
    client, settings, _ = workflow_client
    input_file = settings.input_root / "S01_R1.fastq.gz"
    input_file.write_bytes(b"reads")
    workflow = client.get(
        "/api/workflows/templates/builtin-read-profile"
    ).json()["workflow"]
    payload = {
        "task_id": "task-route-snapshot",
        "workflow": workflow,
        "confirmed_risks": [],
        "input_files": [str(input_file)],
        "database_profile": "test-profile",
        "resolved_manifest_summary": {
            "database_count": 4,
        },
        "compilation_summary": {
            "process_count": 7,
        },
    }

    created = client.post(
        "/api/workflows/execution-snapshots",
        json=payload,
    )
    assert created.status_code == 201
    snapshot = created.json()
    fetched = client.get(
        "/api/workflows/execution-snapshots/task-route-snapshot"
    )
    assert fetched.status_code == 200
    assert fetched.json()["snapshot_sha256"] == (
        snapshot["snapshot_sha256"]
    )

    duplicate = client.post(
        "/api/workflows/execution-snapshots",
        json=payload,
    )
    assert duplicate.status_code == 409


def test_invalid_proposal_identifier_is_rejected_without_state_file(
    workflow_client,
):
    client, settings, _ = workflow_client

    response = client.get(
        "/api/workflows/ai/proposals/invalid!"
    )

    assert response.status_code == 422
    proposal_root = settings.state_root / "workflow_ai_proposals"
    assert list(proposal_root.glob("*.lock")) == []


def _fastqc_workflow() -> dict:
    return {
        "schema_version": "1.0",
        "nodes": [
            {"id": "input", "type": "fastq_input"},
            {"id": "qc", "type": "fastqc"},
        ],
        "edges": [
            {
                "id": "input-qc",
                "source_node": "input",
                "source_port": "reads",
                "target_node": "qc",
                "target_port": "reads",
            }
        ],
        "accepted_risks": [],
    }


def test_dynamic_workflow_run_routes_compile_create_and_reload(workflow_client):
    client, settings, manifest = workflow_client

    response = client.post(
        "/api/workflows/runs",
        json={
            "manifest_path": str(manifest),
            "workflow": _fastqc_workflow(),
        },
    )

    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "queued"
    assert [node["node_id"] for node in run["nodes"]] == ["input", "qc"]
    assert Path(run["compiled_root"]).is_relative_to(settings.state_root)
    fetched = client.get(f"/api/workflows/runs/{run['task_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["graph_hash"] == run["graph_hash"]


def test_dynamic_workflow_run_start_is_explicit_when_auto_run_is_disabled(
    workflow_client,
):
    client, _, manifest = workflow_client
    run = client.post(
        "/api/workflows/runs",
        json={
            "manifest_path": str(manifest),
            "workflow": _fastqc_workflow(),
        },
    ).json()
    submitted: list[str] = []
    client.app.state.dynamic_workflow_runner.submit = (
        lambda task_id: submitted.append(task_id) or True
    )

    started = client.post(f"/api/workflows/runs/{run['task_id']}/start")

    assert started.status_code == 202
    assert started.json() == {"task_id": run["task_id"], "submitted": True}
    assert submitted == [run["task_id"]]


def test_dynamic_workflow_run_routes_report_validation_and_missing_runs(
    workflow_client,
):
    client, _, manifest = workflow_client
    invalid = _fastqc_workflow()
    invalid["edges"] = []

    response = client.post(
        "/api/workflows/runs",
        json={"manifest_path": str(manifest), "workflow": invalid},
    )

    assert response.status_code == 422
    assert client.get("/api/workflows/runs/missing").status_code == 404
    assert client.get("/api/workflows/runs/missing/logs").status_code == 404
