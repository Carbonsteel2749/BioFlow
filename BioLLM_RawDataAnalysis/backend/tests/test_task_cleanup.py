import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture
def case(tmp_path):
    settings = Settings(input_root=tmp_path / "incoming", state_root=tmp_path / "runtime",
                        workflow_path=tmp_path / "main.nf", auto_run=False)
    with TestClient(create_app(settings)) as client:
        repo = client.app.state.repository
        task = str(uuid.uuid4())
        original = settings.input_root / "original.fastq"
        original.write_text("original")
        manifest = settings.input_root / "samples.csv"
        manifest.write_text(f"sample_id,read1,read2\nS,{original},{original}\n")
        repo.create_task(task, str(manifest), ["validate", "report"])
        repo.update_task(task, status="cancelled")
        paths = {}
        for category in ("work", "outputs", "logs"):
            path = settings.state_root / category / task
            path.mkdir()
            (path / "data.txt").write_text(category)
            paths[category] = path
        yield client, task, settings, paths, original


def execute(client, task, mode):
    preview = client.get(f"/api/tasks/{task}/cleanup-preview", params={"mode": mode})
    assert preview.status_code == 200, preview.text
    return client.post(f"/api/tasks/{task}/cleanup", json={
        "mode": mode, "confirmation": task, "token": preview.json()["token"]})


def test_cache_cleanup_keeps_outputs_logs_originals_and_task(case):
    client, task, settings, paths, original = case
    response = execute(client, task, "cache")
    assert response.status_code == 200, response.text
    assert not paths["work"].exists()
    assert paths["outputs"].exists() and paths["logs"].exists() and original.exists()
    assert client.get(f"/api/tasks/{task}").status_code == 200
    assert response.json()["removed_bytes"] > 0


def test_delete_removes_only_task_owned_data(case):
    client, task, settings, paths, original = case
    other = settings.state_root / "work" / "other"
    other.mkdir()
    (other / "keep").write_text("keep")
    assert execute(client, task, "delete").status_code == 200
    assert all(not path.exists() for path in paths.values())
    assert original.exists() and other.exists()
    assert client.get(f"/api/tasks/{task}").status_code == 404


@pytest.mark.parametrize("state", ["queued", "validating", "running"])
def test_active_tasks_cannot_be_cleaned(case, state):
    client, task, _, paths, _ = case
    client.app.state.repository.update_task(task, status=state)
    response = client.get(f"/api/tasks/{task}/cleanup-preview?mode=delete")
    assert response.status_code == 409
    assert paths["work"].exists()


def test_references_block_delete_but_allow_cache_cleanup(case):
    client, task, _, paths, _ = case
    url = f"/api/tasks/{task}/references/paper/draft-1"
    assert client.put(url).status_code == 200
    response = client.get(f"/api/tasks/{task}/cleanup-preview?mode=delete")
    assert response.status_code == 409
    assert "paper" in response.text
    assert execute(client, task, "cache").status_code == 200
    assert paths["outputs"].exists()
    assert client.delete(url).status_code == 200
    assert execute(client, task, "delete").status_code == 200


def test_confirm_token_rechecks_new_references(case):
    client, task, _, paths, _ = case
    preview = client.get(f"/api/tasks/{task}/cleanup-preview?mode=delete").json()
    client.put(f"/api/tasks/{task}/references/paper/new")
    response = client.post(f"/api/tasks/{task}/cleanup", json={
        "mode": "delete", "confirmation": task, "token": preview["token"]})
    assert response.status_code == 409
    assert paths["outputs"].exists()


def test_confirmation_is_required(case):
    client, task, _, paths, _ = case
    response = client.post(f"/api/tasks/{task}/cleanup", json={
        "mode": "delete", "confirmation": task, "token": "invented"})
    assert response.status_code == 409
    assert paths["work"].exists()


def test_symlink_root_is_refused_and_nested_link_does_not_follow(case):
    client, task, settings, paths, original = case
    (paths["work"] / "external").symlink_to(original)
    assert execute(client, task, "cache").status_code == 200
    assert original.read_text() == "original"
    paths["work"].symlink_to(settings.input_root, target_is_directory=True)
    assert client.get(f"/api/tasks/{task}/cleanup-preview?mode=delete").status_code == 409
    assert original.exists()


def test_cleanup_failure_keeps_task_visible(case, monkeypatch):
    from backend.app.services import task_cleanup
    client, task, _, paths, _ = case
    def fail(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(task_cleanup.shutil, "rmtree", fail)
    response = execute(client, task, "delete")
    assert response.status_code == 409
    assert client.get(f"/api/tasks/{task}").status_code == 200
    assert paths["outputs"].exists()


@pytest.mark.skipif(sys.platform != "linux", reason="server process verification uses /proc")
def test_paused_task_with_residual_process_is_blocked(case):
    client, task, _, paths, _ = case
    client.app.state.repository.update_task(task, status="paused")
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"], cwd=paths["work"])
    try:
        response = client.get(f"/api/tasks/{task}/cleanup-preview?mode=delete")
        assert response.status_code == 409
        assert paths["work"].exists()
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_other_manifest_using_output_blocks_deletion(case):
    client, task, settings, paths, _ = case
    manifest = settings.input_root / "reuse.csv"
    manifest.write_text(f"sample_id,read1,read2\nS,{paths['outputs'] / 'data.txt'},{paths['outputs'] / 'data.txt'}\n")
    client.app.state.repository.create_task(str(uuid.uuid4()), str(manifest), ["validate"])
    assert client.get(f"/api/tasks/{task}/cleanup-preview?mode=delete").status_code == 409


def test_changed_files_require_a_new_preview(case):
    client, task, _, paths, _ = case
    preview = client.get(f"/api/tasks/{task}/cleanup-preview?mode=delete").json()
    (paths["outputs"] / "new.txt").write_text("new result")
    response = client.post(f"/api/tasks/{task}/cleanup", json={
        "mode": "delete", "confirmation": task, "token": preview["token"]})
    assert response.status_code == 409
    assert paths["outputs"].exists()


def test_shared_hardlink_not_counted_as_freed_space(case):
    client, task, _, paths, original = case
    (paths["work"] / "data.txt").unlink()
    os.link(original, paths["work"] / "linked.fastq")
    preview = client.get(f"/api/tasks/{task}/cleanup-preview?mode=cache")
    assert preview.status_code == 200
    assert preview.json()["estimated_bytes"] == 0
    assert execute(client, task, "cache").status_code == 200
    assert original.read_text() == "original"


def test_delete_removes_artifact_records_and_internal_relations(case):
    client, task, _, paths, _ = case
    service = client.app.state.artifact_service
    parent = service.register(task_id=task, producer="report", artifact_type="report.summary",
                              path=paths["outputs"] / "data.txt", media_type="text/plain")
    child = service.register(task_id=task, producer="report", artifact_type="report.summary",
                             path=paths["outputs"] / "data.txt", media_type="text/plain",
                             parent_artifact_ids=[parent["artifact_id"]])
    assert execute(client, task, "delete").status_code == 200
    assert client.get(f"/api/artifacts/{parent['artifact_id']}").status_code == 404
    assert client.get(f"/api/artifacts/{child['artifact_id']}").status_code == 404


def test_even_own_input_reads_inside_outputs_are_protected(case):
    client, task, settings, paths, _ = case
    (settings.input_root / "samples.csv").write_text(
        f"sample_id,read1,read2\nS,{paths['outputs'] / 'data.txt'},{paths['outputs'] / 'data.txt'}\n")
    assert client.get(f"/api/tasks/{task}/cleanup-preview?mode=delete").status_code == 409
    assert paths["outputs"].exists()
