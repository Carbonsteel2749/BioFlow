import csv
import gzip
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.tests.test_uploads_and_preview import make_client


VALIDATOR = Path(__file__).resolve().parents[2] / "workflow/bin/core/validate_manifest.py"


def upload_pair(client, suffix, left="read/1", right="read/2"):
    uploads = []
    for mate, header in ((1, left), (2, right)):
        content = f"@{header}\nACGT\n+\nIIII\n".encode()
        if suffix.endswith(".gz"):
            content = gzip.compress(content)
        response = client.post("/api/uploads/files", files={
            "file": (f"S01_R{mate}{suffix}", content, "application/octet-stream"),
        })
        assert response.status_code == 201
        uploads.append(response.json()["id"])
    return {"files": [{"sample_id": "S01", "read1_upload_id": uploads[0],
                       "read2_upload_id": uploads[1]}]}


def validate(manifest, tmp_path):
    diagnostic = tmp_path / "diagnostic.json"
    result = subprocess.run([
        sys.executable, str(VALIDATOR), "--manifest", str(manifest),
        "--output", str(tmp_path / "validated.csv"), "--diagnostic", str(diagnostic),
    ], capture_output=True, text=True)
    return result.returncode, json.loads(diagnostic.read_text())


@pytest.mark.parametrize("suffix", [".fastq.gz", ".fq.gz", ".fastq", ".fq"])
def test_uploaded_manifest_passes_real_workflow_validator(tmp_path, suffix):
    client, settings = make_client(tmp_path)
    with client:
        payload = upload_pair(client, suffix)
        response = client.post("/api/uploads/manifests", json=payload)
        assert response.status_code == 201
        manifest = Path(response.json()["manifest_path"])
        code, diagnostic = validate(manifest, tmp_path)
        assert code == 0, diagnostic
        with manifest.open() as handle:
            row = next(csv.DictReader(handle))
        for mate in (1, 2):
            alias = Path(row[f"read{mate}"])
            original = settings.input_root / "uploads" / payload["files"][0][f"read{mate}_upload_id"] / f"reads{suffix}"
            assert alias.name == f"S01_R{mate}{suffix}"
            assert not alias.is_symlink()
            assert os.path.samefile(alias, original)
        # Previously uploaded files remain reusable in independent manifests.
        again = client.post("/api/uploads/manifests", json=payload)
        assert again.status_code == 201
        assert again.json()["manifest_path"] != str(manifest)
        assert validate(Path(again.json()["manifest_path"]), tmp_path)[0] == 0


@pytest.mark.parametrize("left,right,reason", [
    ("read/2", "read/1", "expected mate 1"),
    ("first/1", "second/2", "read identifiers differ"),
])
def test_generated_names_do_not_bypass_read_pair_validation(tmp_path, left, right, reason):
    client, _ = make_client(tmp_path)
    with client:
        response = client.post("/api/uploads/manifests", json=upload_pair(client, ".fastq.gz", left, right))
        assert response.status_code == 201
        code, diagnostic = validate(Path(response.json()["manifest_path"]), tmp_path)
        assert code == 65
        assert diagnostic["stage"] == "fastq_pairing"
        assert reason in diagnostic["reason"]


def test_link_failure_rolls_back_manifest_without_removing_uploads(tmp_path, monkeypatch):
    client, settings = make_client(tmp_path)
    with client:
        payload = upload_pair(client, ".fastq.gz")
        real_link = os.link
        calls = 0

        def fail_second(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("test link failure")
            real_link(source, destination)

        monkeypatch.setattr("backend.app.services.uploads.os.link", fail_second)
        response = client.post("/api/uploads/manifests", json=payload)
        assert response.status_code == 422
        assert list((settings.input_root / "manifests").iterdir()) == []
        assert len(list((settings.input_root / "uploads").glob("*/reads.fastq.gz"))) == 2
