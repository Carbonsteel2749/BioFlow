import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.services.llm_diagnostics import (
    Diagnostic,
    DiagnosticResponseError,
)
from backend.app.services.runner import (
    _read_tail,
    build_nextflow_command,
)


class SafeTransientDiagnosticService:
    def diagnose_failure(self, **_):
        return Diagnostic(
            classification="transient_process",
            confidence=0.99,
            summary="检测到一次性连接中断。",
            possible_causes=("对端临时重置连接。",),
            recommended_actions=("完成小样本验证后仅恢复一次。",),
            needs_user_approval=False,
            raw_model_output='{"classification":"transient_process"}',
        )


class MalformedDiagnosticService:
    def diagnose_failure(self, **_):
        raise DiagnosticResponseError(
            "invalid model response",
            raw_model_output='{"password":"model-secret","summary":"broken"}',
        )


STEPS = [
    "validate",
    "fastqc_raw",
    "fastp",
    "host_depletion",
    "taxonomy",
    "functional_annotation",
    "report",
]


@pytest.fixture
def environment(tmp_path: Path):
    input_root = tmp_path / "incoming"
    state_root = tmp_path / "state"
    workflow_path = tmp_path / "workflow" / "main.nf"
    input_root.mkdir()
    workflow_path.parent.mkdir()
    workflow_path.write_text("nextflow.enable.dsl=2\n", encoding="utf-8")
    settings = Settings(
        input_root=input_root,
        state_root=state_root,
        workflow_path=workflow_path,
        nextflow_bin="nextflow",
        auto_run=False,
        default_host_index=tmp_path / "databases" / "GRCh38_noalt_as",
    )
    manifest = input_root / "samples.csv"
    manifest.write_text(
        "sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n",
        encoding="utf-8",
    )
    return settings, manifest


@pytest.fixture
def client(environment):
    settings, _ = environment
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_create_task_records_seven_ordered_steps(client, environment):
    _, manifest = environment

    response = client.post("/api/tasks", json={"manifest_path": str(manifest)})

    assert response.status_code == 201
    created = response.json()
    assert created["status"] == "queued"
    detail = client.get(f"/api/tasks/{created['id']}")
    assert detail.status_code == 200
    assert [step["name"] for step in detail.json()["steps"]] == STEPS
    assert all(step["status"] == "pending" for step in detail.json()["steps"])


def test_create_task_rejects_path_outside_approved_input_root(client, tmp_path: Path):
    manifest = tmp_path / "outside.csv"
    manifest.write_text("sample_id,read1,read2\n", encoding="utf-8")

    response = client.post("/api/tasks", json={"manifest_path": str(manifest)})

    assert response.status_code == 422
    assert response.json()["detail"] == "manifest_path is outside the approved input root"


def test_create_task_rejects_non_csv_manifest(client, environment):
    settings, _ = environment
    invalid = settings.input_root / "samples.txt"
    invalid.write_text("sample_id,read1,read2\n", encoding="utf-8")

    response = client.post("/api/tasks", json={"manifest_path": str(invalid)})

    assert response.status_code == 422
    assert response.json()["detail"] == "manifest_path must point to a .csv file"


def test_task_state_survives_application_restart(environment):
    settings, manifest = environment
    with TestClient(create_app(settings)) as first:
        created = first.post("/api/tasks", json={"manifest_path": str(manifest)}).json()

    with TestClient(create_app(settings)) as second:
        response = second.get(f"/api/tasks/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["status"] == "queued"


def test_cancelling_a_task_persists_terminal_state_and_skips_unstarted_steps(client, environment):
    _, manifest = environment
    task_id = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()["id"]

    response = client.post(f"/api/tasks/{task_id}/cancel")

    assert response.status_code == 200
    cancelled = response.json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["error_message"] == "cancelled by user"
    assert all(step["status"] == "skipped" for step in cancelled["steps"])
    assert all(step["error_message"] == "cancelled by user" for step in cancelled["steps"])


def test_cancellation_rejects_a_completed_task(client, environment):
    _, manifest = environment
    task_id = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()["id"]
    client.app.state.repository.update_task(task_id, status="completed")

    response = client.post(f"/api/tasks/{task_id}/cancel")

    assert response.status_code == 409
    assert response.json()["detail"] == "task is not eligible for cancellation"


def test_running_task_is_paused_with_recovery_context_after_application_restart(environment):
    settings, manifest = environment
    with TestClient(create_app(settings)) as first:
        created = first.post("/api/tasks", json={"manifest_path": str(manifest)}).json()
        first.app.state.repository.update_task(created["id"], status="running")
        first.app.state.repository.update_step(created["id"], "validate", status="running")

    with TestClient(create_app(settings)) as second:
        restored = second.get(f"/api/tasks/{created['id']}").json()

    assert restored["status"] == "paused"
    assert restored["error_message"] == "API restarted while task was running"
    assert restored["steps"][0]["status"] == "running"


def test_workflow_parameters_are_validated_persisted_and_survive_restart(environment):
    settings, manifest = environment
    payload = {
        "manifest_path": str(manifest),
        "parameters": {
            "threads": 8,
            "fastp_length_required": 75,
            "fastp_correction": True,
            "host_filter_mode": "strict_both_unmapped",
            "host_bowtie2_preset": "very-sensitive",
            "kraken_db": "/srv/databases/kraken2/test",
            "read_length": 100,
            "humann_nucleotide_db": "/srv/databases/humann/chocophlan",
            "humann_protein_db": "/srv/databases/humann/uniref",
            "metaphlan_db": "/srv/databases/metaphlan/current/index",
        },
    }

    with TestClient(create_app(settings)) as first:
        created = first.post("/api/tasks", json=payload)

    assert created.status_code == 201
    parameters = created.json()["parameters"]
    assert parameters["threads"] == 8
    assert parameters["fastp_length_required"] == 75
    assert parameters["fastp_correction"] is True
    assert parameters["host_index"] == str(settings.default_host_index)
    assert parameters["kraken_db"] == "/srv/databases/kraken2/test"
    assert parameters["read_length"] == 100
    assert parameters["humann_nucleotide_db"] == "/srv/databases/humann/chocophlan"
    assert parameters["humann_protein_db"] == "/srv/databases/humann/uniref"
    assert parameters["metaphlan_db"] == "/srv/databases/metaphlan/current/index"

    with TestClient(create_app(settings)) as second:
        restored = second.get(f"/api/tasks/{created.json()['id']}")

    assert restored.status_code == 200
    assert restored.json()["parameters"] == parameters


def test_server_default_kraken_database_is_applied(tmp_path: Path, monkeypatch):
    input_root = tmp_path / "incoming"
    state_root = tmp_path / "state"
    workflow_path = tmp_path / "workflow" / "main.nf"
    kraken_db = tmp_path / "databases" / "kraken2" / "standard_8gb"
    input_root.mkdir()
    workflow_path.parent.mkdir()
    workflow_path.write_text("nextflow.enable.dsl=2\n", encoding="utf-8")
    manifest = input_root / "samples.csv"
    manifest.write_text(
        "sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BIOLLM_INPUT_ROOT", str(input_root))
    monkeypatch.setenv("BIOLLM_STATE_ROOT", str(state_root))
    monkeypatch.setenv("BIOLLM_WORKFLOW_PATH", str(workflow_path))
    monkeypatch.setenv("BIOLLM_KRAKEN_DB", str(kraken_db))
    monkeypatch.setenv("BIOLLM_AUTO_RUN", "0")

    settings = Settings.from_env()
    with TestClient(create_app(settings)) as test_client:
        response = test_client.post("/api/tasks", json={"manifest_path": str(manifest)})

    assert response.status_code == 201
    assert response.json()["parameters"].get("kraken_db") == str(kraken_db)
    assert response.json()["parameters"].get("read_length") == 150


def test_server_default_humann_databases_are_applied(tmp_path: Path, monkeypatch):
    input_root = tmp_path / "incoming"
    state_root = tmp_path / "state"
    workflow_path = tmp_path / "workflow" / "main.nf"
    nucleotide_db = tmp_path / "databases" / "humann" / "chocophlan"
    protein_db = tmp_path / "databases" / "humann" / "uniref"
    metaphlan_db = tmp_path / "databases" / "metaphlan" / "current" / "index"
    input_root.mkdir()
    workflow_path.parent.mkdir()
    workflow_path.write_text("nextflow.enable.dsl=2\n", encoding="utf-8")
    manifest = input_root / "samples.csv"
    manifest.write_text(
        "sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BIOLLM_INPUT_ROOT", str(input_root))
    monkeypatch.setenv("BIOLLM_STATE_ROOT", str(state_root))
    monkeypatch.setenv("BIOLLM_WORKFLOW_PATH", str(workflow_path))
    monkeypatch.setenv("BIOLLM_HUMANN_NUCLEOTIDE_DB", str(nucleotide_db))
    monkeypatch.setenv("BIOLLM_HUMANN_PROTEIN_DB", str(protein_db))
    monkeypatch.setenv("BIOLLM_METAPHLAN_DB", str(metaphlan_db))
    monkeypatch.setenv("BIOLLM_AUTO_RUN", "0")

    settings = Settings.from_env()
    with TestClient(create_app(settings)) as test_client:
        response = test_client.post("/api/tasks", json={"manifest_path": str(manifest)})

    assert response.status_code == 201
    assert response.json()["parameters"]["humann_nucleotide_db"] == str(nucleotide_db)
    assert response.json()["parameters"]["humann_protein_db"] == str(protein_db)
    assert response.json()["parameters"]["metaphlan_db"] == str(metaphlan_db)


def test_server_managed_reference_override_is_rejected(
    tmp_path: Path,
):
    input_root = tmp_path / "incoming"
    state_root = tmp_path / "state"
    workflow_path = tmp_path / "workflow" / "main.nf"
    database_root = tmp_path / "databases"
    database_manifest = tmp_path / "database.resolved.json"
    input_root.mkdir()
    workflow_path.parent.mkdir()
    workflow_path.write_text("nextflow.enable.dsl=2\n", encoding="utf-8")
    manifest = input_root / "samples.csv"
    manifest.write_text(
        "sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n",
        encoding="utf-8",
    )
    database_manifest.write_text(
        json.dumps(
            {
                "databases": {
                    "taxonomy_reads": {
                        "kraken2": {"path": str(database_root / "kraken")}
                    },
                    "function_reads": {
                        "humann_nucleotide": {
                            "path": str(database_root / "chocophlan")
                        },
                        "humann_protein": {
                            "path": str(database_root / "uniref")
                        },
                        "metaphlan": {
                            "path": str(database_root / "metaphlan")
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        input_root=input_root,
        state_root=state_root,
        workflow_path=workflow_path,
        auto_run=False,
        default_host_index=database_root / "host" / "GRCh38",
        default_database_manifest=database_manifest,
    )

    with TestClient(create_app(settings)) as test_client:
        response = test_client.post(
            "/api/tasks",
            json={
                "manifest_path": str(manifest),
                "parameters": {"kraken_db": "/unapproved/kraken"},
            },
        )

    assert response.status_code == 422
    assert "kraken_db is server-managed" in response.json()["detail"]


def test_resolved_database_manifest_paths_are_recorded_in_task(tmp_path: Path):
    input_root = tmp_path / "incoming"
    state_root = tmp_path / "state"
    workflow_path = tmp_path / "workflow" / "main.nf"
    database_root = tmp_path / "databases"
    database_manifest = tmp_path / "database.resolved.json"
    input_root.mkdir()
    workflow_path.parent.mkdir()
    workflow_path.write_text("nextflow.enable.dsl=2\n", encoding="utf-8")
    manifest = input_root / "samples.csv"
    manifest.write_text(
        "sample_id,read1,read2\nS01,S01_R1.fastq.gz,S01_R2.fastq.gz\n",
        encoding="utf-8",
    )
    database_manifest.write_text(
        json.dumps(
            {
                "databases": {
                    "taxonomy_reads": {
                        "kraken2": {"path": str(database_root / "kraken")}
                    },
                    "function_reads": {
                        "humann_nucleotide": {
                            "path": str(database_root / "chocophlan")
                        },
                        "humann_protein": {
                            "path": str(database_root / "uniref")
                        },
                        "metaphlan": {
                            "path": str(database_root / "metaphlan")
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        input_root=input_root,
        state_root=state_root,
        workflow_path=workflow_path,
        auto_run=False,
        default_database_manifest=database_manifest,
    )

    with TestClient(create_app(settings)) as test_client:
        response = test_client.post(
            "/api/tasks",
            json={"manifest_path": str(manifest)},
        )

    assert response.status_code == 201
    parameters = response.json()["parameters"]
    assert parameters["kraken_db"] == str((database_root / "kraken").resolve())
    assert parameters["humann_nucleotide_db"] == str(
        (database_root / "chocophlan").resolve()
    )
    assert parameters["humann_protein_db"] == str(
        (database_root / "uniref").resolve()
    )
    assert parameters["metaphlan_db"] == str(
        (database_root / "metaphlan").resolve()
    )


@pytest.mark.parametrize(
    "parameters",
    [
        {"threads": 0},
        {"fastp_qualified_quality_phred": 94},
        {"host_max_removed_pct": 101},
        {"host_filter_mode": "unsafe-mode"},
    ],
)
def test_create_task_rejects_invalid_workflow_parameters(client, environment, parameters):
    _, manifest = environment

    response = client.post(
        "/api/tasks",
        json={"manifest_path": str(manifest), "parameters": parameters},
    )

    assert response.status_code == 422


def test_log_endpoint_allows_only_known_step_and_redacts_home_path(client, environment):
    settings, manifest = environment
    task_id = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()["id"]
    log_dir = settings.state_root / "logs" / task_id
    log_dir.mkdir(parents=True)
    (log_dir / "fastp.log").write_text(
        "reading /home/xh/private/S01.fastq.gz\nfinished\n",
        encoding="utf-8",
    )

    invalid = client.get(f"/api/tasks/{task_id}/logs", params={"step": "../../etc/passwd"})
    valid = client.get(f"/api/tasks/{task_id}/logs", params={"step": "fastp"})

    assert invalid.status_code == 422
    assert valid.status_code == 200
    assert "/home/xh" not in valid.json()["text"]
    assert "[HOME]" in valid.json()["text"]


def test_log_endpoint_reads_workflow_step_logs_and_redacts_credentials(
    client,
    environment,
):
    settings, manifest = environment
    task_id = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()["id"]
    log_dir = settings.state_root / "outputs" / task_id / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "S01.fastp.log").write_text(
        "S01 Authorization: Bearer secret-token /home/xh/private\n",
        encoding="utf-8",
    )

    response = client.get(f"/api/tasks/{task_id}/logs", params={"step": "fastp"})

    assert response.status_code == 200
    text = response.json()["text"]
    assert "S01" not in text
    assert "secret-token" not in text
    assert "/home/xh" not in text
    assert "[SAMPLE_001]" in text


def test_log_reader_drops_a_truncated_partial_line(client, environment):
    settings, manifest = environment
    task_id = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()["id"]
    log_dir = settings.state_root / "logs" / task_id
    log_dir.mkdir(parents=True)
    log_path = log_dir / "fastp.log"
    log_path.write_bytes(
        (b"x" * 2048)
        + b"password=boundary-secret\nvisible-tail\n"
    )

    text = client.app.state.service.read_log(task_id, "fastp", max_bytes=128)

    assert text.endswith("visible-tail\n")
    assert "secret" not in text


def test_failure_tail_drops_a_truncated_partial_line(tmp_path):
    source = tmp_path / "large-runner.log"
    source.write_bytes(
        (b"x" * 128)
        + b"password=boundary-secret\nvisible-tail"
    )

    excerpt = _read_tail(source, 24)

    assert excerpt.endswith("visible-tail")
    assert "secret" not in excerpt


def test_retry_requires_failed_task_with_explicit_permission(client, environment):
    _, manifest = environment
    task_id = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()["id"]

    response = client.post(f"/api/tasks/{task_id}/retry")

    assert response.status_code == 409
    assert response.json()["detail"] == "task is not eligible for automatic retry"


def test_build_nextflow_command_uses_argument_list(environment):
    settings, manifest = environment

    command = build_nextflow_command(
        settings=settings,
        task_id="12345678-1234-5678-1234-567812345678",
        manifest_path=manifest,
        parameters={
            "threads": 8,
            "fastp_correction": True,
            "fastp_cut_front": False,
            "host_index": str(settings.default_host_index),
            "host_filter_mode": "strict_both_unmapped",
            "kraken_db": "/srv/databases/kraken2/standard_8gb",
            "read_length": 150,
            "humann_nucleotide_db": "/srv/databases/humann/chocophlan",
            "humann_protein_db": "/srv/databases/humann/uniref",
            "metaphlan_db": "/srv/databases/metaphlan/current/index",
        },
    )

    assert isinstance(command, list)
    assert command[:3] == ["nextflow", "run", str(settings.workflow_path)]
    assert "--input_manifest" in command
    assert str(manifest.resolve()) in command
    assert "-work-dir" in command
    assert command[command.index("--threads") + 1] == "8"
    assert command[command.index("--fastp_correction") + 1] == "true"
    assert command[command.index("--fastp_cut_front") + 1] == "false"
    assert command[command.index("--host_index") + 1] == str(settings.default_host_index)
    assert command[command.index("--kraken_db") + 1] == "/srv/databases/kraken2/standard_8gb"
    assert command[command.index("--read_length") + 1] == "150"
    assert command[command.index("--humann_nucleotide_db") + 1] == "/srv/databases/humann/chocophlan"
    assert command[command.index("--humann_protein_db") + 1] == "/srv/databases/humann/uniref"
    assert command[command.index("--metaphlan_db") + 1] == "/srv/databases/metaphlan/current/index"
    assert all(";" not in part for part in command)


def test_retry_command_uses_resume_and_server_database_manifest(environment):
    settings, manifest = environment
    database_manifest = settings.state_root / "database.resolved.json"
    database_manifest.parent.mkdir(parents=True, exist_ok=True)
    database_manifest.write_text("{}\n", encoding="utf-8")
    configured = Settings(
        input_root=settings.input_root,
        state_root=settings.state_root,
        workflow_path=settings.workflow_path,
        nextflow_bin=settings.nextflow_bin,
        auto_run=False,
        default_database_manifest=database_manifest,
    )

    command = build_nextflow_command(
        configured,
        "12345678-1234-5678-1234-567812345678",
        manifest,
        resume=True,
    )

    assert command[-1] == "-resume"
    assert command[command.index("--database_manifest") + 1] == str(
        database_manifest.resolve()
    )


def test_missing_nextflow_pauses_task_and_creates_alert(environment):
    settings, manifest = environment
    missing = Settings(
        input_root=settings.input_root,
        state_root=settings.state_root,
        workflow_path=settings.workflow_path,
        nextflow_bin="definitely-not-installed-nextflow",
        auto_run=False,
        poll_interval_seconds=0.01,
    )
    app = create_app(missing)
    task = app.state.service.create_task(str(manifest))

    app.state.runner.run(task["id"])

    paused = app.state.service.get_task(task["id"])
    assert paused["status"] == "paused"
    assert paused["steps"][0]["status"] == "failed"
    assert paused["retry_allowed"] is False
    assert "executable not found" in paused["error_message"]
    assert paused["alerts"][0]["level"] == "error"


def test_audit_persistence_failure_pauses_task_and_disables_retry(environment):
    settings, manifest = environment
    missing = Settings(
        input_root=settings.input_root,
        state_root=settings.state_root,
        workflow_path=settings.workflow_path,
        nextflow_bin="definitely-not-installed-nextflow",
        auto_run=False,
        poll_interval_seconds=0.01,
    )
    app = create_app(missing)
    task = app.state.service.create_task(str(manifest))
    audit_root = settings.state_root / "diagnostics"
    audit_root.mkdir(parents=True)
    outside = settings.state_root / "outside-audit.jsonl"
    outside.write_text("must-not-change\n", encoding="utf-8")
    (audit_root / f"{task['id']}.jsonl").symlink_to(outside)

    app.state.runner.run(task["id"])

    paused = app.state.service.get_task(task["id"])
    assert paused["status"] == "paused"
    assert paused["retry_allowed"] is False
    assert paused["error_message"] == "Diagnostic audit persistence failed"
    assert "诊断审计记录写入失败" in paused["alerts"][-1]["message"]
    assert outside.read_text(encoding="utf-8") == "must-not-change\n"


def test_safe_failure_completes_one_bounded_automatic_resume(
    environment,
    monkeypatch,
):
    settings, manifest = environment
    configured = Settings(
        input_root=settings.input_root,
        state_root=settings.state_root,
        workflow_path=settings.workflow_path,
        nextflow_bin=settings.nextflow_bin,
        auto_run=False,
        auto_retry_enabled=True,
        retry_validation_manifest=manifest,
    )
    app = create_app(configured)
    app.state.runner.diagnostic_service = SafeTransientDiagnosticService()
    task = app.state.service.create_task(str(manifest))
    calls = []

    def fake_run_nextflow(*, command, task_id, output_dir, runner_log):
        calls.append(list(command))
        if len(calls) == 1:
            app.state.repository.update_task(task_id, current_step="fastp")
            runner_log.write_text(
                "connection reset by peer; exit status 75\n",
                encoding="utf-8",
            )
            return 75
        assert command[-1] == "-resume"
        archive = output_dir / "deliverables" / f"{task_id}.tar.gz"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"verified-test-archive")
        return 0

    monkeypatch.setattr(app.state.runner, "_run_nextflow", fake_run_nextflow)
    monkeypatch.setattr(
        app.state.runner,
        "_run_retry_validation",
        lambda _task_id, _task: True,
    )

    app.state.runner.run(task["id"])

    completed = app.state.service.get_task(task["id"])
    assert completed["status"] == "completed"
    assert completed["retry_count"] == 1
    assert completed["retry_allowed"] is False
    assert len(calls) == 2
    rows = [
        json.loads(line)
        for line in (
            configured.state_root
            / "diagnostics"
            / f"{task['id']}.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert [row["event"] for row in rows] == [
        "diagnosis_completed",
        "retry_validation_completed",
        "automatic_retry_authorized",
        "automatic_retry_started",
        "automatic_retry_finished",
    ]
    diagnosis = rows[0]
    assert diagnosis["policy_decision"]["allowed"] is True
    assert diagnosis["final_decision"]["allowed"] is False
    assert diagnosis["final_decision"]["action"] == "validation_pending"
    assert diagnosis["modifications"] == []
    assert rows[2]["final_decision"]["allowed"] is True
    assert rows[3]["result"] == "running"
    assert rows[3]["retry_count"] == 1


def test_failed_retry_validation_records_pause_as_final_decision(
    environment,
    monkeypatch,
):
    settings, manifest = environment
    configured = Settings(
        input_root=settings.input_root,
        state_root=settings.state_root,
        workflow_path=settings.workflow_path,
        nextflow_bin=settings.nextflow_bin,
        auto_run=False,
        auto_retry_enabled=True,
        retry_validation_manifest=manifest,
    )
    app = create_app(configured)
    app.state.runner.diagnostic_service = SafeTransientDiagnosticService()
    task = app.state.service.create_task(str(manifest))
    calls = []

    def fake_run_nextflow(*, command, task_id, output_dir, runner_log):
        calls.append(list(command))
        app.state.repository.update_task(task_id, current_step="fastp")
        runner_log.write_text(
            "connection reset by peer; exit status 75\n",
            encoding="utf-8",
        )
        return 75

    monkeypatch.setattr(app.state.runner, "_run_nextflow", fake_run_nextflow)
    monkeypatch.setattr(
        app.state.runner,
        "_run_retry_validation",
        lambda _task_id, _task: False,
    )

    app.state.runner.run(task["id"])

    paused = app.state.service.get_task(task["id"])
    assert paused["status"] == "paused"
    assert paused["retry_count"] == 0
    assert paused["retry_allowed"] is False
    assert len(calls) == 1
    rows = [
        json.loads(line)
        for line in (
            configured.state_root
            / "diagnostics"
            / f"{task['id']}.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert [row["event"] for row in rows] == [
        "diagnosis_completed",
        "retry_validation_completed",
    ]
    validation = rows[-1]
    assert validation["policy_decision"]["allowed"] is True
    assert validation["final_decision"]["allowed"] is False
    assert validation["final_decision"]["category"] == "validation_failed"
    assert validation["final_decision"]["action"] == "pause_and_notify"
    assert validation["modifications"] == []


def test_malformed_model_output_is_redacted_and_audited(
    environment,
    monkeypatch,
):
    settings, manifest = environment
    app = create_app(settings)
    app.state.runner.diagnostic_service = MalformedDiagnosticService()
    task = app.state.service.create_task(str(manifest))

    def fake_run_nextflow(*, command, task_id, output_dir, runner_log):
        app.state.repository.update_task(task_id, current_step="fastp")
        runner_log.write_text("tool returned exit status 1\n", encoding="utf-8")
        return 1

    monkeypatch.setattr(app.state.runner, "_run_nextflow", fake_run_nextflow)

    app.state.runner.run(task["id"])

    paused = app.state.service.get_task(task["id"])
    assert paused["status"] == "paused"
    audit_text = (
        settings.state_root
        / "diagnostics"
        / f"{task['id']}.jsonl"
    ).read_text(encoding="utf-8")
    assert "model-secret" not in audit_text
    assert "[REDACTED]" in audit_text
    row = json.loads(audit_text)
    assert row["parsed_diagnostic"]["classification"] == "unknown"
    assert row["final_decision"]["allowed"] is False


def test_approved_failed_task_can_retry_only_once(client, environment):
    _, manifest = environment
    task_id = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()["id"]
    repository = client.app.state.repository
    repository.update_task(task_id, status="failed", retry_allowed=True)
    repository.update_step(task_id, "validate", status="failed")

    first = client.post(f"/api/tasks/{task_id}/retry")
    repository.update_task(task_id, status="failed", retry_allowed=True)
    second = client.post(f"/api/tasks/{task_id}/retry")

    assert first.status_code == 200
    assert first.json()["status"] == "queued"
    assert first.json()["retry_count"] == 1
    assert all(step["status"] == "pending" for step in first.json()["steps"])
    assert second.status_code == 409


def test_result_download_rejects_archive_outside_task_directory(client, environment, tmp_path: Path):
    _, manifest = environment
    task_id = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()["id"]
    outside = tmp_path / "outside.tar.gz"
    outside.write_bytes(b"not-a-real-archive")
    client.app.state.repository.update_task(
        task_id, status="completed", result_archive=str(outside)
    )

    response = client.get(f"/api/tasks/{task_id}/results")

    assert response.status_code == 422
    assert response.json()["detail"] == "result archive is outside the task output directory"


def test_trace_updates_matching_workflow_step(client, environment):
    from backend.app.services.progress import sync_trace

    settings, manifest = environment
    task_id = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()["id"]
    trace = settings.state_root / "outputs" / task_id / "trace.tsv"
    trace.parent.mkdir(parents=True)
    trace.write_text("name\tstatus\nRUN_FASTP\tCOMPLETED\n", encoding="utf-8")

    sync_trace(client.app.state.repository, task_id, trace)

    task = client.get(f"/api/tasks/{task_id}").json()
    fastp = next(step for step in task["steps"] if step["name"] == "fastp")
    assert fastp["status"] == "succeeded"
    assert fastp["progress"] == 100.0


def test_trace_waits_for_all_samples_before_marking_step_succeeded(
    client,
    environment,
):
    settings, manifest = environment
    task_id = client.post("/api/tasks", json={"manifest_path": str(manifest)}).json()["id"]
    trace = settings.state_root / "outputs" / task_id / "trace.tsv"
    trace.parent.mkdir(parents=True)
    trace.write_text(
        "name\tstatus\nFASTP (S01)\tCOMPLETED\nFASTP (S02)\tRUNNING\n",
        encoding="utf-8",
    )

    from backend.app.services.progress import sync_trace

    sync_trace(client.app.state.repository, task_id, trace)

    task = client.get(f"/api/tasks/{task_id}").json()
    fastp = next(step for step in task["steps"] if step["name"] == "fastp")
    assert fastp["status"] == "running"
    assert fastp["progress"] == 50.0

    trace.write_text(
        "name\tstatus\nFASTP (S01)\tCACHED\nFASTP (S02)\tCOMPLETED\n",
        encoding="utf-8",
    )
    sync_trace(client.app.state.repository, task_id, trace)
    task = client.get(f"/api/tasks/{task_id}").json()
    fastp = next(step for step in task["steps"] if step["name"] == "fastp")
    assert fastp["status"] == "succeeded"
    assert fastp["progress"] == 100.0
