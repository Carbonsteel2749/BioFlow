import json
import stat

import pytest

from backend.app.services.diagnostic_audit import append_diagnostic_audit


def test_diagnostic_audit_records_decision_modifications_attempt_and_result(tmp_path):
    state_root = tmp_path / "runtime"
    audit_path = append_diagnostic_audit(
        state_root=state_root,
        task_id="task-001",
        event="diagnosis_completed",
        record={
            "retry_count": 0,
            "schema_version": 99,
            "task_id": "overridden",
            "event": "overridden",
            "result": "paused",
            "model_raw_diagnostic": (
                '{"summary": "sample-alpha at /home/private/data; '
                'token=secret-value; host=192.0.2.10"}'
            ),
            "final_decision": {
                "allowed": False,
                "action": "pause_and_notify",
                "reason": "database missing",
            },
            "modifications": [],
        },
        input_root="/home/private/data",
        sample_identifiers=["sample-alpha"],
    )

    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    rendered = json.dumps(payload, ensure_ascii=False)
    assert payload["schema_version"] == 1
    assert payload["task_id"] == "task-001"
    assert payload["event"] == "diagnosis_completed"
    assert payload["retry_count"] == 0
    assert payload["result"] == "paused"
    assert payload["final_decision"]["action"] == "pause_and_notify"
    assert payload["modifications"] == []
    assert "secret-value" not in rendered
    assert "sample-alpha" not in rendered
    assert "/home/private/data" not in rendered
    assert "192.0.2.10" not in rendered
    assert stat.S_IMODE(audit_path.stat().st_mode) == 0o600


def test_diagnostic_audit_appends_retry_result(tmp_path):
    state_root = tmp_path / "runtime"
    for event, result, retry_count in (
        ("automatic_retry_started", "running", 1),
        ("automatic_retry_finished", "succeeded", 1),
    ):
        append_diagnostic_audit(
            state_root=state_root,
            task_id="task-002",
            event=event,
            record={"retry_count": retry_count, "result": result},
        )

    rows = [
        json.loads(line)
        for line in (state_root / "diagnostics" / "task-002.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [row["event"] for row in rows] == [
        "automatic_retry_started",
        "automatic_retry_finished",
    ]
    assert rows[-1]["result"] == "succeeded"


def test_diagnostic_audit_rejects_unsafe_task_id(tmp_path):
    with pytest.raises(ValueError):
        append_diagnostic_audit(
            state_root=tmp_path,
            task_id="../escape",
            event="diagnosis_completed",
            record={},
        )
