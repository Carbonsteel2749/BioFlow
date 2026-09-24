from pathlib import Path
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture
def artifact_client(tmp_path: Path):
    input_root = tmp_path / "incoming"
    state_root = tmp_path / "runtime"
    workflow_path = tmp_path / "workflow" / "main.nf"
    input_root.mkdir()
    workflow_path.parent.mkdir()
    workflow_path.write_text("nextflow.enable.dsl=2\n", encoding="utf-8")
    settings = Settings(
        input_root=input_root,
        state_root=state_root,
        workflow_path=workflow_path,
        auto_run=False,
    )
    manifest = input_root / "samples.csv"
    manifest.write_text(
        "sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n",
        encoding="utf-8",
    )
    with TestClient(create_app(settings)) as client:
        task_id = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()["id"]
        yield client, settings, task_id


def _register(client: TestClient, settings: Settings, task_id: str, *, artifact_type: str, name: str, content: bytes):
    path = settings.state_root / "outputs" / task_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return client.app.state.artifact_service.register(
        task_id=task_id,
        producer="test-producer",
        artifact_type=artifact_type,
        path=path,
        media_type="image/png" if name.endswith(".png") else "text/tab-separated-values",
    )


def test_list_detail_filter_and_download_routes(artifact_client):
    client, settings, task_id = artifact_client
    table = _register(
        client,
        settings,
        task_id,
        artifact_type="taxonomy.species_abundance",
        name="tables/species.tsv",
        content=b"species\tabundance\n",
    )
    figure = _register(
        client,
        settings,
        task_id,
        artifact_type="figure.taxonomy",
        name="figures/taxonomy.png",
        content=b"png",
    )

    listed = client.get(f"/api/tasks/{task_id}/artifacts")
    assert listed.status_code == 200
    assert {item["artifact_id"] for item in listed.json()} == {
        table["artifact_id"],
        figure["artifact_id"],
    }
    assert all("path" not in item for item in listed.json())

    filtered = client.get(
        f"/api/tasks/{task_id}/artifacts",
        params={"artifact_type": "figure.taxonomy"},
    )
    assert filtered.json() == [figure]

    detail = client.get(f"/api/artifacts/{table['artifact_id']}")
    assert detail.status_code == 200
    assert detail.json() == table

    downloaded = client.get(f"/api/artifacts/{table['artifact_id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == b"species\tabundance\n"
    assert "attachment" in downloaded.headers["content-disposition"]


def test_figure_catalog_only_returns_figures(artifact_client):
    client, settings, task_id = artifact_client
    _register(
        client,
        settings,
        task_id,
        artifact_type="qc.read_counts",
        name="tables/read_counts.tsv",
        content=b"counts",
    )
    figure = _register(
        client,
        settings,
        task_id,
        artifact_type="figure.qc",
        name="figures/qc.png",
        content=b"png",
    )

    response = client.get(f"/api/tasks/{task_id}/figure-catalog")
    assert response.status_code == 200
    assert response.json() == [figure]


def test_artifact_routes_report_missing_for_unknown_task_or_artifact(artifact_client):
    client, _, _ = artifact_client

    assert client.get("/api/tasks/not-a-task/artifacts").status_code == 404
    assert client.get("/api/artifacts/not-an-artifact").status_code == 404
    assert client.get("/api/artifacts/not-an-artifact/download").status_code == 404


def test_download_blocks_restricted_artifact_and_detects_tampering(artifact_client):
    client, settings, task_id = artifact_client
    restricted = _register(
        client,
        settings,
        task_id,
        artifact_type="reads.raw",
        name="reads/S01_R1.fastq.gz",
        content=b"reads",
    )
    denied = client.get(f"/api/artifacts/{restricted['artifact_id']}/download")
    assert denied.status_code == 403

    table = _register(
        client,
        settings,
        task_id,
        artifact_type="functional.pathway_abundance",
        name="tables/pathways.tsv",
        content=b"pathways",
    )
    path = settings.state_root / "outputs" / task_id / "tables/pathways.tsv"
    path.write_bytes(b"changed")
    changed = client.get(f"/api/artifacts/{table['artifact_id']}/download")
    assert changed.status_code == 409


def _stage_plot(settings, task_id, status="COMPLETED", name="taxonomy.png"):
    output = settings.state_root / "outputs" / task_id
    output.mkdir(parents=True, exist_ok=True)
    (output / "trace.tsv").write_text(
        f"name\thash\tstatus\texit\nTAXONOMY_PLOTS (taxonomy-cohort)\tab/123456\t{status}\t0\n")
    work = settings.state_root / "work" / task_id / "ab" / "123456abcdef"
    plots = work / "taxonomy_plots"
    plots.mkdir(parents=True)
    (work / ".exitcode").write_text("0")
    (plots / "taxonomy_plots.provenance.json").write_text(json.dumps({
        "analysis": "taxonomy_plots", "status": "completed", "top_n": 20,
        "plots": {"stacked": {"status": "generated", "output": name}}}))
    return plots


def test_paused_task_publishes_successful_stage_figures_once(artifact_client):
    client, settings, task_id = artifact_client
    client.app.state.repository.update_task(task_id, status="paused", current_step="report")
    plots = _stage_plot(settings, task_id)
    content = b"\x89PNG\r\n\x1a\nfixture"
    (plots / "taxonomy.png").write_bytes(content)
    first = client.get(f"/api/tasks/{task_id}/artifacts").json()
    assert len(first) == 1
    figure = first[0]
    assert figure["artifact_type"] == "figure.taxonomy"
    assert figure["metadata"]["stage_result"] is True
    assert "path" not in figure
    assert client.get(f"/api/tasks/{task_id}/figure-catalog").json() == first
    assert client.get(figure["download_url"]).content == content
    # Published figures survive later intermediate-cache cleanup.
    (plots / "taxonomy.png").unlink()
    assert client.get(figure["download_url"]).content == content
    assert client.get(f"/api/tasks/{task_id}/artifacts").json() == first
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "paused"


@pytest.mark.parametrize("unsafe", ["running", "failed", "traversal", "symlink", "directory_symlink", "invalid_png"])
def test_stage_figures_reject_unfinished_or_unsafe_files(artifact_client, unsafe):
    client, settings, task_id = artifact_client
    name = "../secret.png" if unsafe == "traversal" else "taxonomy.png"
    plots = _stage_plot(settings, task_id, status={"running": "RUNNING", "failed": "FAILED"}.get(unsafe, "COMPLETED"), name=name)
    outside = settings.state_root / "secret.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nsecret")
    path = plots / "taxonomy.png"
    if unsafe == "symlink":
        path.symlink_to(outside)
    elif unsafe == "directory_symlink":
        moved = plots.with_name("moved")
        plots.rename(moved)
        plots.symlink_to(moved, target_is_directory=True)
        path.write_bytes(outside.read_bytes())
    else:
        path.write_bytes(b"not a png" if unsafe == "invalid_png" else outside.read_bytes())
    assert client.get(f"/api/tasks/{task_id}/artifacts").json() == []


def test_skipped_figures_have_explanations_even_without_png(artifact_client):
    client, settings, task_id = artifact_client
    plots = _stage_plot(settings, task_id)
    (plots / 'taxonomy_plots.provenance.json').write_text(json.dumps({
        'analysis':'taxonomy_plots','status':'completed','plots':{
            'pcoa':{'status':'skipped','reason':'Bray-Curtis PCoA requires at least two samples with positive abundance totals','outputs':[]}}}))
    response=client.get(f'/api/tasks/{task_id}/figure-notices')
    assert response.status_code == 200
    assert response.json()[0]['state'] == 'skipped'
    assert '两个' in response.json()[0]['message']
