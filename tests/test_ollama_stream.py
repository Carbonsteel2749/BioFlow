"""The manuscript client must keep reading while a local model is still writing."""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "article_writing"))

from article_writing.llm.ollama import OllamaClient


class _Stream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)
        self.closed = False

    def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)

    def __enter__(self) -> "_Stream":
        return self

    def __exit__(self, *args: object) -> bool:
        self.closed = True
        return False


def test_chat_stream_keeps_partial_text(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def urlopen(request, timeout=0):  # noqa: ANN001
        seen.append(request.full_url)
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["stream"] is True
        assert payload["think"] is False
        assert timeout == 30
        return _Stream(
            [
                b'{"message":{"content":"Methods "},"done":false}\n',
                b'{"message":{"content":"were recorded."},"done":true}\n',
            ]
        )

    monkeypatch.setattr("article_writing.llm.ollama.urllib.request.urlopen", urlopen)
    text = OllamaClient(timeout=30).generate("write the methods")
    assert text == "Methods were recorded."
    assert seen == ["http://127.0.0.1:11434/api/chat"]


def test_timeout_does_not_send_a_second_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def urlopen(request, timeout=0):  # noqa: ANN001
        calls["n"] += 1
        raise TimeoutError("timed out")

    monkeypatch.setattr("article_writing.llm.ollama.urllib.request.urlopen", urlopen)
    with pytest.raises(TimeoutError, match="no text"):
        OllamaClient(timeout=30).generate("write the methods")
    assert calls["n"] == 1


def test_missing_chat_endpoint_uses_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request, timeout=0):  # noqa: ANN001
        if request.full_url.endswith("/api/chat"):
            raise urllib.error.HTTPError(request.full_url, 404, "missing", hdrs=None, fp=io.BytesIO(b""))
        payload = json.loads(request.data.decode("utf-8"))
        assert payload["stream"] is True
        assert "think" not in payload
        return _Stream([b'{"response":"Done.","done":true}\n'])

    monkeypatch.setattr("article_writing.llm.ollama.urllib.request.urlopen", urlopen)
    assert OllamaClient(timeout=30).generate("write", system="be brief") == "Done."
