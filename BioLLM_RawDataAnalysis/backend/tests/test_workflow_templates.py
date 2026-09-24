import hashlib
import json
import stat
from pathlib import Path

import pytest

from backend.app.services.workflow_templates import (
    ExecutionSnapshotConflictError,
    ExecutionSnapshotIntegrityError,
    TemplateVersionConflictError,
    WorkflowTemplateError,
    WorkflowTemplateService,
)


@pytest.fixture
def template_service(tmp_path: Path) -> WorkflowTemplateService:
    input_root = tmp_path / "incoming"
    input_root.mkdir()
    return WorkflowTemplateService(
        tmp_path / "workflow-state",
        input_root,
    )


def _builtin_workflow(
    service: WorkflowTemplateService,
) -> dict:
    return service.get_template(
        "builtin-read-profile"
    )["workflow"]


def test_two_builtin_templates_are_valid_and_read_only(
    template_service: WorkflowTemplateService,
):
    templates = template_service.list_templates()

    assert {
        item["template_id"] for item in templates
    } == {
        "builtin-read-profile",
        "builtin-mag",
    }
    assert all(item["read_only"] for item in templates)
    assert all(
        item["validation"]["can_execute"] for item in templates
    )


def test_save_read_copy_and_update_create_immutable_versions(
    template_service: WorkflowTemplateService,
):
    copied = template_service.copy_template(
        "builtin-read-profile",
        name="研究模板",
        template_id="study-template",
    )
    assert copied["version"] == 1
    assert copied["copied_from"]["template_id"] == (
        "builtin-read-profile"
    )

    changed = copied["workflow"]
    trim = next(
        node for node in changed["nodes"] if node["id"] == "trim"
    )
    trim["parameters"]["threads"] = 12
    updated = template_service.save_template(
        template_id="study-template",
        base_version=1,
        name="研究模板",
        workflow=changed,
    )

    assert updated["version"] == 2
    assert updated["parent_version"] == 1
    assert (
        template_service.get_template(
            "study-template",
            1,
        )["workflow"]["nodes"][2]["parameters"]["threads"]
        == 4
    )
    assert (
        template_service.get_template(
            "study-template",
        )["version"]
        == 2
    )

    with pytest.raises(TemplateVersionConflictError):
        template_service.save_template(
            template_id="study-template",
            base_version=1,
            name="过期更新",
            workflow=changed,
        )


def test_builtin_templates_cannot_be_modified_in_place(
    template_service: WorkflowTemplateService,
):
    with pytest.raises(
        TemplateVersionConflictError,
        match="read-only",
    ):
        template_service.save_template(
            template_id="builtin-read-profile",
            base_version=1,
            name="非法修改",
            workflow=_builtin_workflow(template_service),
        )


def test_template_rejects_unregistered_parameters(
    template_service: WorkflowTemplateService,
):
    workflow = _builtin_workflow(template_service)
    workflow["nodes"][2]["parameters"]["shell"] = "rm -rf /"

    with pytest.raises(
        WorkflowTemplateError,
        match="non-registered parameters",
    ):
        template_service.save_template(
            name="非法参数",
            workflow=workflow,
        )


def test_execution_snapshot_contains_required_reproducibility_data(
    template_service: WorkflowTemplateService,
):
    input_file = template_service.input_root / "S01_R1.fastq.gz"
    input_file.write_bytes(b"test-fastq")
    workflow = _builtin_workflow(template_service)

    snapshot = template_service.create_execution_snapshot(
        task_id="task-001",
        workflow=workflow,
        confirmed_risks=[],
        input_files=[input_file],
        database_profile="production-v1",
        resolved_manifest_summary={
            "database_count": 4,
            "manifest_sha256": "a" * 64,
        },
        compilation_summary={
            "process_count": 7,
            "compiler_version": "1.0.0",
        },
    )

    assert snapshot["workflow"] == workflow
    assert snapshot["confirmed_risks"] == []
    assert snapshot["node_registry_version"] == "1.0.0"
    assert snapshot["database_profile"] == "production-v1"
    assert snapshot["node_parameters"]["trim"]["threads"] == 4
    assert snapshot["input_files"] == [
        {
            "relative_path": "S01_R1.fastq.gz",
            "size_bytes": 10,
            "sha256": hashlib.sha256(
                b"test-fastq"
            ).hexdigest(),
        }
    ]
    persisted = template_service.get_execution_snapshot(
        "task-001"
    )
    assert persisted["snapshot_sha256"] == (
        snapshot["snapshot_sha256"]
    )
    path = (
        template_service.snapshot_root / "task-001.json"
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o440

    with pytest.raises(ExecutionSnapshotConflictError):
        template_service.create_execution_snapshot(
            task_id="task-001",
            workflow=workflow,
            confirmed_risks=[],
            input_files=[input_file],
            database_profile="different",
            resolved_manifest_summary={},
            compilation_summary={},
        )


def test_execution_snapshot_requires_exact_confirmed_risks(
    template_service: WorkflowTemplateService,
):
    input_file = template_service.input_root / "S01.fastq.gz"
    input_file.write_bytes(b"reads")
    risk_key = "risk:host:host_depletion_without_fastp"
    workflow = {
        "schema_version": "1.0",
        "nodes": [
            {
                "id": "input",
                "type": "fastq_input",
                "parameters": {},
            },
            {
                "id": "host",
                "type": "host_depletion",
                "parameters": {},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source_node": "input",
                "source_port": "reads",
                "target_node": "host",
                "target_port": "reads",
            }
        ],
        "accepted_risks": [risk_key],
    }

    with pytest.raises(
        WorkflowTemplateError,
        match="exactly match",
    ):
        template_service.create_execution_snapshot(
            task_id="task-risk-mismatch",
            workflow=workflow,
            confirmed_risks=[],
            input_files=[input_file],
            database_profile="test",
            resolved_manifest_summary={},
            compilation_summary={},
        )

    snapshot = template_service.create_execution_snapshot(
        task_id="task-risk-confirmed",
        workflow=workflow,
        confirmed_risks=[risk_key],
        input_files=[input_file],
        database_profile="test",
        resolved_manifest_summary={},
        compilation_summary={},
    )
    assert snapshot["confirmed_risks"] == [risk_key]


def test_execution_snapshot_rejects_inputs_outside_approved_root(
    template_service: WorkflowTemplateService,
    tmp_path: Path,
):
    outside = tmp_path / "outside.fastq.gz"
    outside.write_bytes(b"reads")

    with pytest.raises(
        WorkflowTemplateError,
        match="outside",
    ):
        template_service.create_execution_snapshot(
            task_id="task-outside",
            workflow=_builtin_workflow(template_service),
            confirmed_risks=[],
            input_files=[outside],
            database_profile="test",
            resolved_manifest_summary={},
            compilation_summary={},
        )


def test_execution_snapshot_detects_persisted_tampering(
    template_service: WorkflowTemplateService,
):
    input_file = template_service.input_root / "reads.fastq.gz"
    input_file.write_bytes(b"reads")
    template_service.create_execution_snapshot(
        task_id="task-tamper",
        workflow=_builtin_workflow(template_service),
        confirmed_risks=[],
        input_files=[input_file],
        database_profile="test",
        resolved_manifest_summary={},
        compilation_summary={},
    )
    path = (
        template_service.snapshot_root / "task-tamper.json"
    )
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["database_profile"] = "tampered"
    path.chmod(0o600)
    path.write_text(
        json.dumps(saved, ensure_ascii=False),
        encoding="utf-8",
    )
    path.chmod(0o440)

    with pytest.raises(ExecutionSnapshotIntegrityError):
        template_service.get_execution_snapshot("task-tamper")


def test_template_read_detects_workflow_checksum_tampering(
    template_service: WorkflowTemplateService,
):
    saved = template_service.copy_template(
        "builtin-read-profile",
        name="待校验模板",
        template_id="tampered-template",
    )
    path = (
        template_service.template_root
        / "tampered-template"
        / "v000001.json"
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    record["workflow"]["nodes"][2]["parameters"]["threads"] = 64
    path.write_text(
        json.dumps(record, ensure_ascii=False),
        encoding="utf-8",
    )

    assert saved["workflow_sha256"] == record["workflow_sha256"]
    with pytest.raises(
        WorkflowTemplateError,
        match="checksum",
    ):
        template_service.get_template("tampered-template")
