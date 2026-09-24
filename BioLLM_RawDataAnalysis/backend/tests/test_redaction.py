import pytest

from backend.app.services.redaction import redact_log, tail_excerpt


def test_redact_log_removes_paths_credentials_network_and_samples():
    text = (
        "reading /home/xh/private/S01_R1.fastq.gz\n"
        "Authorization: Bearer secret-token\n"
        "api_key=abc123 password=hunter2\n"
        "server=219.224.3.96 owner=person@example.org sample=S01\n"
    )

    redacted = redact_log(
        text,
        input_root="/home/xh/private",
        sample_identifiers=["S01"],
    )

    for secret in (
        "/home/xh",
        "secret-token",
        "abc123",
        "hunter2",
        "219.224.3.96",
        "person@example.org",
        "S01",
    ):
        assert secret not in redacted
    assert "[INPUT_ROOT]" in redacted or "[PATH]" in redacted
    assert "[REDACTED]" in redacted
    assert "[IP]" in redacted
    assert "[EMAIL]" in redacted
    assert "[SAMPLE_001]" in redacted


def test_redact_log_removes_json_cli_quoted_and_url_credentials():
    text = (
        '{"password":"json-secret","api_key":"json-key",'
        '"Authorization":"Bearer json-token"}\n'
        "--password 'space separated secret' --client-secret=cli-secret\n"
        "password=\"quoted secret\" auth_token: plain-token\n"
        "postgresql://db-user:db-password@database.internal/study\n"
    )

    redacted = redact_log(text)

    for secret in (
        "json-secret",
        "json-key",
        "json-token",
        "space separated secret",
        "cli-secret",
        "quoted secret",
        "plain-token",
        "db-user",
        "db-password",
    ):
        assert secret not in redacted
    assert redacted.count("[REDACTED]") >= 7


def test_tail_excerpt_is_bounded_and_rejects_invalid_limit():
    excerpt = tail_excerpt("discarded-secret\nvisible", 10)
    assert excerpt.endswith("visible")
    assert "secret" not in excerpt
    no_complete_line = tail_excerpt("password=supersecret", 5)
    assert no_complete_line == "[... earlier log content omitted ...]\n"
    with pytest.raises(ValueError):
        tail_excerpt("abc", 0)

