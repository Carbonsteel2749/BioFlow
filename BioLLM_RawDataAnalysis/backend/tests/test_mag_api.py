import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.services.progress import step_for_process, sync_trace
from backend.app.services.runner import build_nextflow_command


CORE_STEPS = [
    "validate",
    "fastqc_raw",
    "fastp",
    "host_depletion",
    "taxonomy",
    "functional_annotation",
]
MAG_STEPS = [
    "assembly",
    "binning",
    "bin_refinement",
    "bin_quantification",
    "bin_reassembly",
    "bin_annotation",
]


def _environment(tmp_path: Path, *, mag_available: bool) -> tuple[Settings, Path]:
    input_root = tmp_path / "incoming"
    state_root = tmp_path / "state"
    workflow_path = tmp_path / "workflow" / "main.nf"
    database_registry = tmp_path / "database-registry.json"
    database_manifest = tmp_path / "database.resolved.json"
    input_root.mkdir()
    workflow_path.parent.mkdir()
    workflow_path.write_text("nextflow.enable.dsl=2\n", encoding="utf-8")
    database_registry.write_text("{}\n", encoding="utf-8")
    manifest = input_root / "samples.csv"
    manifest.write_text(
        "sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n",
        encoding="utf-8",
    )
    databases = {
        "taxonomy_reads": {
            "kraken2": {"path": str(tmp_path / "databases" / "kraken")}
        },
        "function_reads": {
            "humann_nucleotide": {
                "path": str(tmp_path / "databases" / "chocophlan")
            },
            "humann_protein": {
                "path": str(tmp_path / "databases" / "uniref")
            },
            "metaphlan": {"path": str(tmp_path / "databases" / "metaphlan")},
        },
    }
    if mag_available:
        databases["mag_annotation"] = {
            "classification": {"path": str(tmp_path / "databases" / "gtdb")},
            "function": {"path": str(tmp_path / "databases" / "mag-function")},
        }
    database_manifest.write_text(
        json.dumps(
            {
                "database_profile": "test",
                "capabilities": {
                    "reads_analysis": True,
                    "mag_analysis": mag_available,
                    "mag_unavailable_reason": (
                        None
                        if mag_available
                        else "database profile does not provide complete MAG annotation databases"
                    ),
                },
                "databases": databases,
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        input_root=input_root,
        state_root=state_root,
        workflow_path=workflow_path,
        nextflow_bin="nextflow",
        auto_run=False,
        default_database_registry=database_registry,
        default_database_profile="test",
        default_database_manifest=database_manifest,
    )
    return settings, manifest


def test_capabilities_endpoint_reports_mag_database_readiness(tmp_path: Path):
    settings, _ = _environment(tmp_path, mag_available=False)

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "reads_analysis": True,
        "mag_analysis": False,
        "mag_unavailable_reason": (
            "database profile does not provide complete MAG annotation databases"
        ),
        "database_profile": "test",
        "file_uploads": True,
        "task_cancellation": True,
        "result_preview": True,
    }


def test_task_rejects_mag_request_when_server_profile_is_not_ready(tmp_path: Path):
    settings, manifest = _environment(tmp_path, mag_available=False)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/tasks",
            json={
                "manifest_path": str(manifest),
                "parameters": {"enable_mags": True},
            },
        )

    assert response.status_code == 422
    assert "MAG analysis is unavailable" in response.json()["detail"]


def test_declared_mag_capability_requires_real_manifest_entries(tmp_path: Path):
    settings, manifest = _environment(tmp_path, mag_available=True)
    payload = json.loads(
        settings.default_database_manifest.read_text(encoding="utf-8")
    )
    payload["databases"].pop("mag_annotation")
    settings.default_database_manifest.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    with TestClient(create_app(settings)) as client:
        capability_response = client.get("/api/capabilities")
        task_response = client.post(
            "/api/tasks",
            json={
                "manifest_path": str(manifest),
                "parameters": {"enable_mags": True},
            },
        )

    assert capability_response.json()["mag_analysis"] is False
    assert task_response.status_code == 422


def test_mag_task_records_dynamic_steps_and_parameters(tmp_path: Path):
    settings, manifest = _environment(tmp_path, mag_available=True)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/tasks",
            json={
                "manifest_path": str(manifest),
                "parameters": {
                    "enable_mags": True,
                    "enable_reassembly": True,
                    "mag_threads": 12,
                    "mag_memory_gb": 48,
                    "assembler": "megahit",
                    "bin_completeness": 75,
                    "bin_contamination": 4,
                },
            },
        )

    assert response.status_code == 201
    task = response.json()
    assert [step["name"] for step in task["steps"]] == [
        *CORE_STEPS,
        *MAG_STEPS,
        "report",
    ]
    assert task["parameters"]["enable_mags"] is True
    assert task["parameters"]["mag_threads"] == 12
    assert task["parameters"]["mag_memory_gb"] == 48


def test_mag_task_omits_reassembly_step_when_disabled(tmp_path: Path):
    settings, manifest = _environment(tmp_path, mag_available=True)

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/tasks",
            json={
                "manifest_path": str(manifest),
                "parameters": {
                    "enable_mags": True,
                    "enable_reassembly": False,
                },
            },
        )

    assert response.status_code == 201
    steps = [step["name"] for step in response.json()["steps"]]
    assert "bin_reassembly" not in steps
    assert steps[-2:] == ["bin_annotation", "report"]


def test_nextflow_command_includes_mag_and_server_database_parameters(tmp_path: Path):
    settings, manifest = _environment(tmp_path, mag_available=True)

    command = build_nextflow_command(
        settings,
        "12345678-1234-5678-1234-567812345678",
        manifest,
        {
            "enable_mags": True,
            "enable_reassembly": False,
            "mag_threads": 12,
            "mag_memory_gb": 48,
            "assembler": "metaspades",
            "bin_completeness": 80,
            "bin_contamination": 3,
        },
    )

    expected = {
        "--database_registry": str(settings.default_database_registry.resolve()),
        "--database_profile": "test",
        "--database_manifest": str(settings.default_database_manifest.resolve()),
        "--enable_mags": "true",
        "--enable_reassembly": "false",
        "--mag_threads": "12",
        "--mag_memory_gb": "48",
        "--assembler": "metaspades",
        "--bin_completeness": "80",
        "--bin_contamination": "3",
    }
    for flag, value in expected.items():
        assert command[command.index(flag) + 1] == value


def test_mag_process_names_map_to_distinct_progress_steps():
    expected = {
        "ASSEMBLY (coassembly)": "assembly",
        "BINNING (three tools)": "binning",
        "BIN_REFINEMENT (70_5)": "bin_refinement",
        "BIN_QUANTIFICATION (all samples)": "bin_quantification",
        "BIN_REASSEMBLY (refined bins)": "bin_reassembly",
        "BIN_ANNOTATION (MAGs)": "bin_annotation",
    }

    assert {name: step_for_process(name) for name in expected} == expected


def test_trace_ignores_steps_not_configured_for_task(tmp_path: Path):
    settings, manifest = _environment(tmp_path, mag_available=True)

    with TestClient(create_app(settings)) as client:
        task = client.post(
            "/api/tasks",
            json={"manifest_path": str(manifest)},
        ).json()
        trace = settings.state_root / "outputs" / task["id"] / "trace.tsv"
        trace.parent.mkdir(parents=True)
        trace.write_text(
            "name\tstatus\nASSEMBLY (legacy)\tCOMPLETED\n",
            encoding="utf-8",
        )

        sync_trace(client.app.state.repository, task["id"], trace)

        restored = client.get(f"/api/tasks/{task['id']}").json()
    assert [step["name"] for step in restored["steps"]] == [
        *CORE_STEPS,
        "report",
    ]


def test_mag_step_log_is_available_for_mag_task(tmp_path: Path):
    settings, manifest = _environment(tmp_path, mag_available=True)

    with TestClient(create_app(settings)) as client:
        task = client.post(
            "/api/tasks",
            json={
                "manifest_path": str(manifest),
                "parameters": {"enable_mags": True},
            },
        ).json()
        log_dir = settings.state_root / "outputs" / task["id"] / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "global.assembly.log").write_text(
            "co-assembly started\n",
            encoding="utf-8",
        )
        response = client.get(
            f"/api/tasks/{task['id']}/logs",
            params={"step": "assembly"},
        )

    assert response.status_code == 200
    assert "co-assembly started" in response.json()["text"]
