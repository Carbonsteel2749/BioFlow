from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from .llm_diagnostics import Diagnostic, MANUAL_REVIEW_CLASSIFICATIONS


IDEMPOTENT_STEPS = frozenset(
    {
        "fastqc_raw",
        "fastp",
        "host_depletion",
        "taxonomy",
        "functional_annotation",
        "report",
    }
)
SAFE_CLASSIFICATIONS = frozenset(
    {"transient_process", "temporary_file", "controlled_runtime"}
)
SAFE_TRANSIENT_EXIT_CODES = frozenset({75})
NEVER_RETRY_EXIT_CODES = frozenset(
    {2, 64, 65, 66, 69, 70, 78, 126, 127, 130, 137, 143}
)
_BLOCKING_EVIDENCE = (
    (
        "insufficient_resources",
        re.compile(
            r"(?i)\b(out of memory|oom(?:[-_ ]kill(?:ed)?)?|cannot allocate memory|"
            r"killed process|no space left on device|disk quota exceeded|"
            r"too many open files|memoryerror|exit(?:ed)?(?: with)? "
            r"(?:status|code)[=: ]*137)\b"
        ),
    ),
    (
        "missing_database",
        re.compile(
            r"(?i)(?:(?:database|kraken|bracken|humann|metaphlan|bowtie2 index).{0,100}"
            r"(?:missing|not found|no such file|does not exist|"
            r"does not contain necessary file|unreadable|invalid))|"
            r"(?:(?:missing|not found|no such file).{0,100}(?:database|\.k2d|\.bt2))"
        ),
    ),
    (
        "corrupted_input",
        re.compile(
            r"(?i)\b(invalid fastq|truncated (?:fastq|gzip|input)|unexpected end of file|"
            r"crc (?:error|check failed)|corrupt(?:ed)? (?:gzip|fastq|input)|"
            r"sequence and quality lengths differ|read pairs? (?:do not match|mismatch)|"
            r"checksum mismatch|invalid input manifest)\b"
        ),
    ),
    (
        "biological_threshold",
        re.compile(
            r"(?i)\b(biological threshold|retained (?:read )?pairs?.{0,80}below|"
            r"host.{0,40}removed.{0,80}above|minimum retained pairs?|"
            r"maximum host removal|scientific threshold)\b"
        ),
    ),
)
_SAFE_EVIDENCE = re.compile(
    r"(?i)\b(connection reset by peer|connection timed out|temporary failure in "
    r"name resolution|interrupted system call|stale file handle|text file busy|"
    r"temporary file (?:collision|already exists|busy)|stale (?:lock|temporary) file|"
    r"filealreadyexistsexception|exit(?:ed)?(?: with)? (?:status|code)[=: ]*75)\b"
)


@dataclass(frozen=True)
class RetryDecision:
    allowed: bool
    reason: str
    category: str = "manual_review"
    action: str = "pause_and_notify"
    modifications: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["modifications"] = list(self.modifications)
        return payload


def _blocked_by_evidence(
    exit_code: int | None,
    redacted_log_excerpt: str,
) -> tuple[str, str] | None:
    for category, pattern in _BLOCKING_EVIDENCE:
        if pattern.search(redacted_log_excerpt):
            return category, f"deterministic {category} evidence requires user attention"
    if exit_code in NEVER_RETRY_EXIT_CODES:
        category = "insufficient_resources" if exit_code == 137 else "non_retryable_exit"
        return category, f"exit code {exit_code} is on the never-retry list"
    return None


def evaluate_retry(
    *,
    step: str,
    exit_code: int | None,
    retry_count: int,
    redacted_log_excerpt: str,
    diagnostic: Diagnostic,
    minimum_confidence: float = 0.90,
) -> RetryDecision:
    blocked = _blocked_by_evidence(exit_code, redacted_log_excerpt)
    if blocked is not None:
        category, reason = blocked
        return RetryDecision(False, reason, category=category)
    if retry_count >= 1:
        return RetryDecision(
            False,
            "maximum automatic retry count has been reached",
            category="retry_limit",
        )
    if step not in IDEMPOTENT_STEPS:
        return RetryDecision(
            False,
            f"step {step!r} is not on the idempotent retry allowlist",
            category="non_idempotent_step",
        )
    if diagnostic.classification in MANUAL_REVIEW_CLASSIFICATIONS:
        return RetryDecision(
            False,
            f"{diagnostic.classification} failures require user attention",
            category=diagnostic.classification,
        )
    if diagnostic.classification not in SAFE_CLASSIFICATIONS:
        return RetryDecision(
            False,
            "the model classification is not on the strict safe retry allowlist",
            category="classification_not_allowed",
        )
    if diagnostic.needs_user_approval:
        return RetryDecision(
            False,
            "diagnostic requires user approval",
            category=diagnostic.classification,
        )
    if diagnostic.confidence < minimum_confidence:
        return RetryDecision(
            False,
            f"diagnostic confidence {diagnostic.confidence:.2f} is below "
            f"{minimum_confidence:.2f}",
            category="low_confidence",
        )
    has_local_evidence = (
        exit_code in SAFE_TRANSIENT_EXIT_CODES
        or bool(_SAFE_EVIDENCE.search(redacted_log_excerpt))
    )
    if not has_local_evidence:
        return RetryDecision(
            False,
            "no deterministic safe transient evidence was found in the exit code or log",
            category="insufficient_safe_evidence",
        )
    return RetryDecision(
        True,
        "one unchanged resume retry is allowed after configured validation succeeds",
        category=diagnostic.classification,
        action="validate_then_resume_once",
        modifications=(
            "add the server-controlled Nextflow -resume flag",
            "keep the input manifest and workflow parameters unchanged",
            "do not delete files or change source, databases, resources, or scientific thresholds",
        ),
    )
