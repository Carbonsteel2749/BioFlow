import json

import httpx
import pytest

from backend.app.services.llm_diagnostics import (
    DiagnosticResponseError,
    OllamaDiagnosticService,
    unavailable_diagnostic,
)
from backend.app.services.retry_policy import evaluate_retry


def test_ollama_diagnostic_requires_structured_allowed_output():
    observed = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        content = {
            "classification": "transient_process",
            "confidence": 0.96,
            "summary": "进程遇到一次性连接中断。",
            "possible_causes": ["对端临时重置了连接。"],
            "recommended_actions": ["先执行小样本验证，再保持参数不变恢复一次。"],
            "needs_user_approval": False,
        }
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(content)}},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = OllamaDiagnosticService(client=client)

    diagnostic = service.diagnose_failure(
        step="functional_annotation",
        log_excerpt="connection reset by peer; exit status 75",
        exit_code=75,
        retry_count=0,
    )

    assert diagnostic.classification == "transient_process"
    assert diagnostic.confidence == 0.96
    assert diagnostic.possible_causes == ("对端临时重置了连接。",)
    assert '"classification": "transient_process"' in diagnostic.raw_model_output
    assert observed["model"] == "qwen3:14b"
    assert observed["stream"] is False
    assert observed["think"] is False
    assert observed["format"]["additionalProperties"] is False
    assert 0 < observed['options']['num_predict'] <= 512
    assert observed['options']['num_ctx'] >= 2048
    assert 'connection reset by peer' in observed['messages'][1]['content']


def test_ollama_diagnostic_rejects_unknown_fields():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "classification": "unknown",
                            "confidence": 0.2,
                            "summary": "unknown",
                            "possible_causes": ["unknown"],
                            "recommended_actions": ["review"],
                            "needs_user_approval": True,
                            "command": "rm -rf /",
                        }
                    )
                }
            },
        )

    service = OllamaDiagnosticService(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with pytest.raises(ValueError):
        service.diagnose_failure(
            step="fastp",
            log_excerpt="failure",
            exit_code=1,
            retry_count=0,
        )


def test_ollama_diagnostic_service_unavailable_has_safe_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("local model unavailable", request=request)

    service = OllamaDiagnosticService(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with pytest.raises(httpx.ConnectError):
        service.diagnose_failure(
            step="fastp",
            log_excerpt="exit status 137",
            exit_code=137,
            retry_count=0,
        )

    fallback = unavailable_diagnostic("ConnectError")
    decision = evaluate_retry(
        step="fastp",
        exit_code=137,
        retry_count=0,
        redacted_log_excerpt="exit status 137",
        diagnostic=fallback,
    )
    assert fallback.classification == "unknown"
    assert fallback.confidence == 0.0
    assert fallback.needs_user_approval is True
    assert decision.allowed is False


@pytest.mark.parametrize(
    "response_body",
    [
        {"message": {"content": "not-json"}},
        {"message": {"content": "[]"}},
        {"message": {}},
    ],
)
def test_ollama_diagnostic_rejects_malformed_responses(response_body):
    service = OllamaDiagnosticService(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=response_body)
            )
        )
    )

    with pytest.raises(ValueError):
        service.diagnose_failure(
            step="taxonomy",
            log_excerpt="tool failure",
            exit_code=1,
            retry_count=0,
        )


def test_malformed_structured_diagnostic_preserves_raw_model_output():
    raw = "not-json-model-output"
    service = OllamaDiagnosticService(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"message": {"content": raw}},
                )
            )
        )
    )

    with pytest.raises(DiagnosticResponseError) as caught:
        service.diagnose_failure(
            step="taxonomy",
            log_excerpt="tool failure",
            exit_code=1,
            retry_count=0,
        )

    assert caught.value.raw_model_output == raw


@pytest.mark.parametrize(
    ("response", "expected_raw"),
    [
        (httpx.Response(200, text="not-an-ollama-json"), "not-an-ollama-json"),
        (httpx.Response(503, text="model temporarily unavailable"), "model temporarily unavailable"),
    ],
)
def test_invalid_ollama_envelope_preserves_raw_response(response, expected_raw):
    service = OllamaDiagnosticService(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: response)
        )
    )

    with pytest.raises(DiagnosticResponseError) as caught:
        service.diagnose_failure(
            step="taxonomy",
            log_excerpt="tool failure",
            exit_code=1,
            retry_count=0,
        )

    assert caught.value.raw_model_output == expected_raw


def test_unavailable_diagnostic_retains_only_bounded_raw_model_output():
    raw = "x" * 20001

    diagnostic = unavailable_diagnostic(
        "invalid response",
        raw_model_output=raw,
    )

    assert diagnostic.raw_model_output.startswith("x" * 20000)
    assert diagnostic.raw_model_output.endswith("[... model response truncated ...]")
    assert len(diagnostic.raw_model_output) < len(raw) + 100


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("confidence", "0.99"),
        ("summary", 137),
    ],
)
def test_ollama_diagnostic_rejects_wrong_scalar_types(field, value):
    content = {
        "classification": "transient_process",
        "confidence": 0.99,
        "summary": "短暂失败。",
        "possible_causes": ["连接中断。"],
        "recommended_actions": ["查看脱敏日志。"],
        "needs_user_approval": False,
    }
    content[field] = value
    service = OllamaDiagnosticService(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"message": {"content": json.dumps(content)}},
                )
            )
        )
    )

    with pytest.raises(ValueError):
        service.diagnose_failure(
            step="taxonomy",
            log_excerpt="connection reset by peer",
            exit_code=75,
            retry_count=0,
        )


def test_low_confidence_diagnostic_cannot_trigger_automatic_retry():
    content = {
        "classification": "transient_process",
        "confidence": 0.50,
        "summary": "连接可能发生了短暂中断。",
        "possible_causes": ["对端临时重置连接。"],
        "recommended_actions": ["请先查看脱敏日志。"],
        "needs_user_approval": False,
    }
    service = OllamaDiagnosticService(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"message": {"content": json.dumps(content)}},
                )
            )
        )
    )

    diagnostic = service.diagnose_failure(
        step="functional_annotation",
        log_excerpt="connection reset by peer; exit status 75",
        exit_code=75,
        retry_count=0,
    )
    decision = evaluate_retry(
        step="functional_annotation",
        exit_code=75,
        retry_count=0,
        redacted_log_excerpt="connection reset by peer; exit status 75",
        diagnostic=diagnostic,
    )

    assert diagnostic.confidence == 0.50
    assert decision.allowed is False
    assert "below" in decision.reason


def test_diagnostic_requiring_user_approval_cannot_trigger_automatic_retry():
    content = {
        "classification": "controlled_runtime",
        "confidence": 0.99,
        "summary": "需要用户确认运行时操作。",
        "possible_causes": ["运行状态需要人工确认。"],
        "recommended_actions": ["请用户审核后再决定。"],
        "needs_user_approval": True,
    }
    service = OllamaDiagnosticService(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"message": {"content": json.dumps(content)}},
                )
            )
        )
    )

    diagnostic = service.diagnose_failure(
        step="functional_annotation",
        log_excerpt="connection reset by peer; exit status 75",
        exit_code=75,
        retry_count=0,
    )
    decision = evaluate_retry(
        step="functional_annotation",
        exit_code=75,
        retry_count=0,
        redacted_log_excerpt="connection reset by peer; exit status 75",
        diagnostic=diagnostic,
    )

    assert diagnostic.needs_user_approval is True
    assert decision.allowed is False
    assert decision.reason == "diagnostic requires user approval"


def test_unsafe_model_recommendations_are_filtered_but_raw_output_is_preserved():
    content = {
        "classification": "temporary_file",
        "confidence": 0.97,
        "summary": "报告文件发生临时冲突。",
        "possible_causes": ["同名报告文件已经存在。"],
        "recommended_actions": [
            "请检查脱敏日志。",
            "如果文件不再需要，可手动删除以允许流程重新生成。",
            "调整工作流配置允许覆盖。",
        ],
        "needs_user_approval": False,
    }
    service = OllamaDiagnosticService(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"message": {"content": json.dumps(content)}},
                )
            )
        )
    )

    diagnostic = service.diagnose_failure(
        step="report",
        log_excerpt="FileAlreadyExistsException",
        exit_code=1,
        retry_count=0,
    )

    assert diagnostic.recommended_actions[0] == "请检查脱敏日志。"
    assert any(
        "不会自动删除文件" in action
        for action in diagnostic.recommended_actions
    )
    assert all(
        "手动删除以" not in action and "调整工作流" not in action
        for action in diagnostic.recommended_actions
    )
    raw_actions = json.loads(diagnostic.raw_model_output)["recommended_actions"]
    assert any(
        "手动删除以允许流程重新生成" in action for action in raw_actions
    )
