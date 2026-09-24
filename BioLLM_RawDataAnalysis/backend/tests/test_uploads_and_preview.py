import csv
import gzip
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


def make_client(tmp_path: Path) -> tuple[TestClient, Settings]:
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
    return TestClient(create_app(settings)), settings


def fastq_bytes(read_name: str) -> bytes:
    return gzip.compress(f"@{read_name}\nACGT\n+\nIIII\n".encode())


def upload(client: TestClient, name: str, read_name: str) -> dict:
    response = client.post(
        "/api/uploads/files",
        files={"file": (name, fastq_bytes(read_name), "application/gzip")},
    )
    assert response.status_code == 201
    return response.json()


def test_capabilities_advertise_upload_cancel_and_preview(tmp_path: Path):
    client, _ = make_client(tmp_path)

    with client:
        payload = client.get("/api/capabilities").json()

    assert payload["file_uploads"] is True
    assert payload["task_cancellation"] is True
    assert payload["result_preview"] is True


def test_task_cancellation_is_idempotent(tmp_path: Path):
    client, settings = make_client(tmp_path)
    manifest = settings.input_root / "samples.csv"
    manifest.write_text(
        "sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n",
        encoding="utf-8",
    )

    with client:
        task = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()
        first = client.post(f"/api/tasks/{task['id']}/cancel")
        second = client.post(f"/api/tasks/{task['id']}/cancel")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "cancelled"


def test_uploaded_pairs_create_server_managed_manifest_and_task(tmp_path: Path):
    client, settings = make_client(tmp_path)

    with client:
        r1 = upload(client, "S01_R1.fastq.gz", "S01/1")
        r2 = upload(client, "S01_R2.fastq.gz", "S01/2")
        response = client.post(
            "/api/uploads/manifests",
            json={
                "files": [
                    {
                        "sample_id": "S01",
                        "read1_upload_id": r1["id"],
                        "read2_upload_id": r2["id"],
                    }
                ]
            },
        )

        assert response.status_code == 201
        manifest_payload = response.json()
        assert manifest_payload["sample_count"] == 1
        manifest = Path(manifest_payload["manifest_path"])
        assert manifest.is_file()
        assert manifest.resolve().is_relative_to(settings.input_root.resolve())
        rows = list(csv.DictReader(manifest.open(encoding="utf-8")))
        assert rows[0]["sample_id"] == "S01"
        assert Path(rows[0]["read1"]).is_file()
        assert Path(rows[0]["read2"]).is_file()

        task = client.post(
            "/api/tasks", json={"manifest_path": str(manifest)}
        )

    assert task.status_code == 201
    assert r1["original_name"] == "S01_R1.fastq.gz"
    assert r1["status"] == "validated"
    assert r1["checksum"].startswith("sha256:")
    assert r1["size"] == len(fastq_bytes("S01/1"))


def test_upload_rejects_non_fastq_and_malformed_gzip(tmp_path: Path):
    client, _ = make_client(tmp_path)

    with client:
        wrong_extension = client.post(
            "/api/uploads/files",
            files={"file": ("notes.txt", b"not-fastq", "text/plain")},
        )
        malformed = client.post(
            "/api/uploads/files",
            files={"file": ("S01_R1.fastq.gz", b"not-gzip", "application/gzip")},
        )

    assert wrong_extension.status_code == 422
    assert malformed.status_code == 422


def test_manifest_rejects_unknown_upload_and_duplicate_sample(tmp_path: Path):
    client, _ = make_client(tmp_path)

    with client:
        unknown = client.post(
            "/api/uploads/manifests",
            json={
                "files": [
                    {
                        "sample_id": "S01",
                        "read1_upload_id": "missing-r1",
                        "read2_upload_id": "missing-r2",
                    }
                ]
            },
        )
        r1 = upload(client, "S01_R1.fastq.gz", "S01/1")
        r2 = upload(client, "S01_R2.fastq.gz", "S01/2")
        duplicate = client.post(
            "/api/uploads/manifests",
            json={
                "files": [
                    {"sample_id": "S01", "read1_upload_id": r1["id"], "read2_upload_id": r2["id"]},
                    {"sample_id": "S01", "read1_upload_id": r1["id"], "read2_upload_id": r2["id"]},
                ]
            },
        )

    assert unknown.status_code == 422
    assert duplicate.status_code == 422


def test_result_preview_is_bounded_and_multiqc_has_csp(tmp_path: Path):
    client, settings = make_client(tmp_path)
    manifest = settings.input_root / "samples.csv"
    manifest.write_text(
        "sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n",
        encoding="utf-8",
    )

    with client:
        task = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()
        output = settings.state_root / "outputs" / task["id"]
        report = output / "report" / "multiqc_report.html"
        report.parent.mkdir(parents=True)
        report.write_text("<html><body>MultiQC</body></html>", encoding="utf-8")
        taxonomy = output / "taxonomy" / "S01" / "species_abundance.tsv"
        taxonomy.parent.mkdir(parents=True)
        taxonomy.write_text(
            "sample_id\ttaxonomy\tabundance\n"
            + "".join(
                f"S01\tSpecies {number:02d}\t{number / 100:.2f}\n"
                for number in range(1, 31)
            ),
            encoding="utf-8",
        )
        ko = output / "functional_annotation" / "S01" / "read_ko.tsv"
        ko.parent.mkdir(parents=True)
        ko.write_text(
            "function_id\tabundance\n"
            + "".join(f"K{number:05d}\t{number}\n" for number in range(30)),
            encoding="utf-8",
        )

        preview = client.get(f"/api/tasks/{task['id']}/preview")
        multiqc = client.get(f"/api/tasks/{task['id']}/reports/multiqc")

    assert preview.status_code == 200
    body = preview.json()
    assert body["multiqc_url"].endswith("/reports/multiqc")
    assert len(body["taxonomy_top"]) == 20
    assert body["taxonomy_top"][0] == {"sample_id": "S01", "name": "Species 30", "abundance": 0.3}
    assert body['taxonomy_measurement']['status'] == 'unknown'
    assert len(body["ko"]["rows"]) == 20
    assert body["ko"]["total_rows"] == 30
    assert multiqc.status_code == 200
    assert "default-src 'none'" in multiqc.headers["content-security-policy"]


def test_preview_does_not_follow_output_symlink(tmp_path: Path):
    client, settings = make_client(tmp_path)
    manifest = settings.input_root / "samples.csv"
    manifest.write_text(
        "sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n",
        encoding="utf-8",
    )
    outside = tmp_path / "outside.tsv"
    outside.write_text("function_id\tabundance\nSECRET\t1\n", encoding="utf-8")

    with client:
        task = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()
        linked = settings.state_root / "outputs" / task["id"] / "read_ko.tsv"
        linked.parent.mkdir(parents=True)
        linked.symlink_to(outside)
        response = client.get(f"/api/tasks/{task['id']}/preview")

    assert response.status_code == 404
    assert response.json()["detail"] == "result preview is not available"


def test_nextflow_process_uses_task_output_as_session_directory(tmp_path: Path):
    client, _ = make_client(tmp_path)
    output = tmp_path / "task-output"
    output.mkdir()
    marker = tmp_path / "observed-cwd.txt"
    runner_log = tmp_path / "runner.log"

    with client:
        return_code = client.app.state.runner._run_nextflow(
            command=[
                sys.executable,
                "-c",
                "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(str(pathlib.Path.cwd()))",
                str(marker),
            ],
            task_id="isolated-task",
            output_dir=output,
            runner_log=runner_log,
        )

    assert return_code == 0
    assert marker.read_text(encoding="utf-8") == str(output.resolve())
