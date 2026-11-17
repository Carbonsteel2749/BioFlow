from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fcntl", reason="Workflow filesystem routes require POSIX")
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.services.template_preflight import preflight_workflow
from backend.app.services.workflow_templates import (
    TemplateNotFoundError, TemplateVersionConflictError,
    WorkflowTemplateError, WorkflowTemplateService,
)


@pytest.fixture
def service(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    return WorkflowTemplateService(tmp_path / "state", incoming)


@pytest.fixture
def client(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    settings = Settings(
        input_root=incoming, state_root=tmp_path / "state",
        workflow_path=tmp_path / "unused.nf", auto_run=False,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_basics_have_complete_metadata_and_independent_copies(service):
    ids = ["builtin-data-quality", "builtin-qc-host", "builtin-species-composition"]
    assert [item["template_id"] for item in service.list_templates()][:3] == ids
    for template_id in ids:
        builtin = service.get_template(template_id)
        assert builtin["version"] == 1
        assert builtin["read_only"]
        assert builtin["input_requirements"] and builtin["expected_outputs"]
        copied = service.copy_template(template_id, name="copy")
        assert copied["input_requirements"] == builtin["input_requirements"]
        assert copied["expected_outputs"] == builtin["expected_outputs"]
        copied["workflow"]["nodes"][1]["parameters"]["threads"] = 8
        updated = service.save_template(
            template_id=copied["template_id"], base_version=1,
            name="changed", workflow=copied["workflow"],
        )
        assert updated["version"] == 2
        assert updated["copied_from"] == copied["copied_from"]
        assert updated["expected_outputs"] == builtin["expected_outputs"]
        assert service.get_template(template_id) == builtin


def test_edit_and_delete_never_change_execution_snapshot(service):
    copied = service.copy_template("builtin-data-quality", name="copy")
    reads = service.input_root / "r1.fastq.gz"
    reads.write_bytes(b"fixture")
    snapshot = service.create_execution_snapshot(
        task_id="snapshot-1", workflow=copied["workflow"],
        confirmed_risks=[], input_files=[reads], database_profile="test",
        resolved_manifest_summary={}, compilation_summary={},
    )
    snapshot_bytes = (service.snapshot_root / "snapshot-1.json").read_bytes()
    copied["workflow"]["nodes"][1]["parameters"]["threads"] = 8
    service.save_template(
        template_id=copied["template_id"], base_version=1,
        name="changed", workflow=copied["workflow"],
    )
    with pytest.raises(TemplateVersionConflictError):
        service.delete_template(copied["template_id"], base_version=1)
    service.delete_template(copied["template_id"], base_version=2)
    assert service.get_execution_snapshot("snapshot-1") == snapshot
    assert (service.snapshot_root / "snapshot-1.json").read_bytes() == snapshot_bytes
    assert all(item["template_id"] != copied["template_id"] for item in service.list_templates())
    for version in [None, 1, 2]:
        with pytest.raises(TemplateNotFoundError):
            service.get_template(copied["template_id"], version)
    with pytest.raises(TemplateNotFoundError):
        service.save_template(template_id=copied["template_id"], name="resurrect", workflow=copied["workflow"])
    restarted = WorkflowTemplateService(service.root, service.input_root)
    with pytest.raises(TemplateNotFoundError):
        restarted.copy_template(copied["template_id"], name="copy again")


@pytest.mark.parametrize("template_id", [
    "builtin-data-quality", "builtin-qc-host", "builtin-species-composition",
    "builtin-read-profile", "builtin-mag",
])
def test_builtin_mutations_rejected(client, template_id):
    template = client.get(f"/api/workflows/templates/{template_id}").json()
    assert client.delete(f"/api/workflows/templates/{template_id}?base_version=1").status_code == 409
    response = client.put(f"/api/workflows/templates/{template_id}", json={
        "name": "overwrite", "workflow": template["workflow"], "base_version": 1,
    })
    assert response.status_code == 409
    assert client.get(f"/api/workflows/templates/{template_id}").json() == template


@pytest.mark.parametrize("case", [
    "unknown_method", "missing_node", "wrong_port", "wrong_type", "cycle",
    "unknown_parameter", "wrong_parameter_type", "below_minimum", "above_maximum",
    "enum", "empty_name", "empty_graph", "bad_metadata",
])
def test_invalid_input_is_clear_and_never_persisted(client, case):
    graph = client.get("/api/workflows/templates/builtin-qc-host").json()["workflow"]
    payload = {"name": "invalid", "workflow": graph}
    if case == "unknown_method":
        graph["nodes"][1]["type"] = "unregistered_method"
    elif case == "missing_node":
        graph["edges"][0]["target_node"] = "absent"
    elif case == "wrong_port":
        graph["edges"][0]["source_port"] = "absent"
    elif case == "wrong_type":
        graph["edges"][1]["source_port"] = "metrics"
    elif case == "cycle":
        graph["edges"].append(dict(id="cycle", source_node="host", source_port="reads", target_node="trim", target_port="reads"))
    elif case == "unknown_parameter":
        graph["nodes"][1]["parameters"]["shell"] = "echo forbidden"
    elif case == "wrong_parameter_type":
        graph["nodes"][1]["parameters"]["threads"] = True
    elif case == "below_minimum":
        graph["nodes"][1]["parameters"]["threads"] = 0
    elif case == "above_maximum":
        graph["nodes"][1]["parameters"]["threads"] = 257
    elif case == "enum":
        graph["nodes"][2]["parameters"]["filter_mode"] = "invalid"
    elif case == "empty_name":
        payload["name"] = "   "
    elif case == "empty_graph":
        graph["nodes"] = []
    elif case == "bad_metadata":
        payload["input_requirements"] = [" "]
    response = client.post("/api/workflows/templates", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]
    assert len(client.get("/api/workflows/templates").json()) == 12


def test_crud_api_and_stale_delete(client):
    original = client.get("/api/workflows/templates/builtin-data-quality").json()
    created = client.post("/api/workflows/templates", json={
        "name": "new", "description": "purpose", "workflow": original["workflow"],
        "input_requirements": ["paired FASTQ"], "expected_outputs": ["FastQC reports"],
    })
    assert created.status_code == 201
    template = created.json()
    path = "/api/workflows/templates/" + template["template_id"]
    updated = client.put(path, json={
        "name": "edited", "description": "edited purpose",
        "workflow": template["workflow"], "base_version": 1,
    })
    assert updated.status_code == 200
    assert updated.json()["input_requirements"] == ["paired FASTQ"]
    assert updated.json()["version"] == 2
    assert client.get(path + "?version=1").json()["name"] == "new"
    assert client.delete(path + "?base_version=1").status_code == 409
    assert client.delete(path).status_code == 422
    assert client.delete(path + "?base_version=2").json()["deleted"]
    assert client.get(path).status_code == 404
    assert client.post(path + "/copy", json={"name": "copy"}).status_code == 404
    assert client.put(path, json={"name": "x", "base_version": 2, "workflow": template["workflow"]}).status_code == 404
    assert client.delete(path + "?base_version=2").status_code == 404


def test_submitted_run_compilation_unchanged_after_template_mutations(client):
    service = client.app.state.workflow_template_service
    copied = service.copy_template("builtin-data-quality", name="run")
    manifest = service.input_root / "samples.csv"
    manifest.write_text("sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n")
    response = client.post("/api/workflows/runs", json={
        "manifest_path": str(manifest), "workflow": copied["workflow"],
    })
    assert response.status_code == 201
    run = response.json()
    root = Path(run["compiled_root"])
    files = {name: (root / name).read_bytes() for name in ["parameters.json", "main.nf", "summary.json", "nextflow.config"]}
    copied["workflow"]["nodes"][1]["parameters"]["threads"] = 8
    service.save_template(template_id=copied["template_id"], base_version=1, name="edit", workflow=copied["workflow"])
    service.delete_template(copied["template_id"], base_version=2)
    for name, data in files.items():
        assert (root / name).read_bytes() == data
    assert client.get("/api/workflows/runs/" + run["task_id"]).json()["graph_hash"] == run["graph_hash"]


def test_preflight_reuses_dependency_check_and_never_assumes_ready(client):
    response = client.post("/api/workflows/templates/builtin-species-composition/preflight?version=1")
    assert response.status_code == 200
    result = response.json()
    assert not result["ready"]
    assert any(check["name"] == "host.grch38_bowtie2" and check["status"] == "blocked" for check in result["checks"])
    assert any(check["name"] == "database_registry" and check["status"] == "blocked" for check in result["checks"])
    assert client.post("/api/workflows/templates/missing/preflight").status_code == 404


def test_preflight_reports_missing_executable(service, tmp_path):
    settings = Settings(
        input_root=service.input_root, state_root=tmp_path,
        workflow_path=tmp_path / "unused.nf",
        nextflow_bin="biollm-test-unavailable-executable",
    )
    result = preflight_workflow(service.get_template("builtin-data-quality")["workflow"], service.registry, settings)
    assert not result["ready"]
    assert any(check["name"] == settings.nextflow_bin and check["status"] == "blocked" for check in result["checks"])


def test_preflight_calls_existing_database_resolver(service, tmp_path, monkeypatch):
    import backend.app.services.template_preflight as module
    calls = []
    resolved = {"databases": {"taxonomy_reads": {}}}
    def resolve(path, profile, mags):
        calls.append((path, profile, mags))
        return resolved
    monkeypatch.setattr(module, "_database_validator", lambda path: {
        "resolve_registry": resolve, "load_json": lambda path: resolved,
    })
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    settings = Settings(
        input_root=service.input_root, state_root=tmp_path, workflow_path=tmp_path / "unused.nf",
        default_database_registry=tmp_path / "registry.json",
        default_database_profile="test", default_database_manifest=tmp_path / "resolved.json",
    )
    result = preflight_workflow(service.get_template("builtin-species-composition")["workflow"], service.registry, settings)
    assert calls == [(settings.default_database_registry, "test", False)]
    assert any(check["name"] == "database_registry" and check["status"] == "passed" for check in result["checks"])
    assert not result["ready"]  # No host index is configured.


def test_preflight_checks_bound_host_index_shards(service, tmp_path, monkeypatch):
    import backend.app.services.template_preflight as module
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=0))
    index = tmp_path / 'reference'
    for suffix in ('1', '2', '3', '4', 'rev.1', 'rev.2'):
        Path(str(index) + '.' + suffix + '.bt2').write_bytes(b'index fixture')
    settings = Settings(input_root=service.input_root, state_root=tmp_path,
                        workflow_path=tmp_path / 'unused.nf', default_host_index=index)
    graph = service.get_template('builtin-qc-host')['workflow']
    assert preflight_workflow(graph, service.registry, settings)['ready']
    Path(str(index) + '.rev.2.bt2').unlink()
    assert not preflight_workflow(graph, service.registry, settings)['ready']


def test_template_save_uses_shared_validator(service, monkeypatch):
    import backend.app.services.workflow_templates as module
    def reject(*args, **kwargs):
        raise WorkflowTemplateError("shared validation rejection")
    graph = service.get_template("builtin-data-quality")["workflow"]
    monkeypatch.setattr(module, "validate_workflow_document", reject)
    with pytest.raises(WorkflowTemplateError, match="shared validation rejection"):
        service.save_template(name="must validate", workflow=graph)


def test_concurrent_save_delete_cannot_resurrect_template(service):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    other = WorkflowTemplateService(service.root, service.input_root)
    copied = service.copy_template("builtin-data-quality", name="concurrent")
    gate = Barrier(2)
    def save():
        gate.wait(timeout=5)
        try:
            return service.save_template(
                template_id=copied["template_id"], base_version=1,
                name="edited", workflow=copied["workflow"],
            )
        except TemplateNotFoundError:
            return "deleted"
    def delete():
        gate.wait(timeout=5)
        try:
            return other.delete_template(copied["template_id"], base_version=1)
        except TemplateVersionConflictError:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved, deleted = pool.submit(save), pool.submit(delete)
        saved, deleted = saved.result(timeout=10), deleted.result(timeout=10)
    if saved == "deleted":
        assert deleted["deleted"]
        with pytest.raises(TemplateNotFoundError):
            service.get_template(copied["template_id"])
    else:
        assert deleted == "stale"
        assert service.get_template(copied["template_id"])["version"] == 2


def test_list_tolerates_concurrent_delete(service, monkeypatch):
    copied = service.copy_template("builtin-data-quality", name="concurrent")
    original = service.get_template
    def disappeared(template_id, version=None):
        if template_id == copied["template_id"]:
            raise TemplateNotFoundError(template_id)
        return original(template_id, version)
    monkeypatch.setattr(service, "get_template", disappeared)
    assert len(service.list_templates()) == 12
