import pytest

from backend.app.services.llm_diagnostics import Diagnostic
from backend.app.services.retry_policy import evaluate_retry


def diagnostic(
    classification: str = "transient_process",
    confidence: float = 0.95,
    needs_user_approval: bool = False,
) -> Diagnostic:
    return Diagnostic(
        classification=classification,
        confidence=confidence,
        summary="用户可读摘要",
        possible_causes=("可能原因",),
        recommended_actions=("建议操作",),
        needs_user_approval=needs_user_approval,
    )


def test_retry_requires_model_and_deterministic_transient_evidence():
    allowed = evaluate_retry(
        step="functional_annotation",
        exit_code=75,
        retry_count=0,
        redacted_log_excerpt="connection reset by peer; exit status 75",
        diagnostic=diagnostic(),
    )
    no_evidence = evaluate_retry(
        step="functional_annotation",
        exit_code=1,
        retry_count=0,
        redacted_log_excerpt="tool failed",
        diagnostic=diagnostic(),
    )

    assert allowed.allowed is True
    assert allowed.action == "validate_then_resume_once"
    assert "keep the input manifest" in allowed.modifications[1]
    assert no_evidence.allowed is False


def test_retry_rejects_second_attempt_low_confidence_and_manual_category():
    second = evaluate_retry(
        step="fastp",
        exit_code=75,
        retry_count=1,
        redacted_log_excerpt="connection reset by peer",
        diagnostic=diagnostic(),
    )
    low_confidence = evaluate_retry(
        step="fastp",
        exit_code=75,
        retry_count=0,
        redacted_log_excerpt="connection reset by peer",
        diagnostic=diagnostic(confidence=0.89),
    )
    manual = evaluate_retry(
        step="fastp",
        exit_code=75,
        retry_count=0,
        redacted_log_excerpt="connection reset by peer",
        diagnostic=diagnostic("missing_database", 0.99, True),
    )

    assert second.allowed is False
    assert second.category == "retry_limit"
    assert low_confidence.allowed is False
    assert manual.allowed is False
    assert manual.category == "missing_database"


def test_real_temporary_file_collision_is_strictly_allowlisted():
    decision = evaluate_retry(
        step="report",
        exit_code=1,
        retry_count=0,
        redacted_log_excerpt=(
            "Caused by: java.nio.file.FileAlreadyExistsException: "
            "[PATH]/nextflow-report.html"
        ),
        diagnostic=diagnostic("temporary_file", 0.97),
    )

    assert decision.allowed is True
    assert decision.category == "temporary_file"
    assert any("do not delete" in item.lower() for item in decision.modifications)


@pytest.mark.parametrize(
    ("log_excerpt", "exit_code", "expected_category"),
    [
        (
            'kraken2: database ("[PATH]") does not contain necessary file taxo.k2d',
            2,
            "missing_database",
        ),
        (
            "gzip: [PATH]/reads.fastq.gz: unexpected end of file",
            1,
            "corrupted_input",
        ),
        (
            "Process exited with status 137 after out of memory",
            137,
            "insufficient_resources",
        ),
        (
            "host removed percentage above maximum host removal threshold",
            1,
            "biological_threshold",
        ),
        (
            "received SIGTERM after user cancellation",
            143,
            "non_retryable_exit",
        ),
    ],
)
def test_blocking_evidence_overrides_an_unsafe_model_allow(
    log_excerpt,
    exit_code,
    expected_category,
):
    decision = evaluate_retry(
        step="functional_annotation",
        exit_code=exit_code,
        retry_count=0,
        redacted_log_excerpt=log_excerpt,
        diagnostic=diagnostic("transient_process", 0.99),
    )

    assert decision.allowed is False
    assert decision.action == "pause_and_notify"
    assert decision.category == expected_category
    assert decision.modifications == ()

