"""Ollama HTTP client (local models; default BioFLow recommendation)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_thinking(text: str) -> str:
    cleaned = _THINK_RE.sub("", text or "")
    return cleaned.strip()


class OllamaClient:
    """Talks to Ollama ``/api/chat`` (preferred) with ``/api/generate`` fallback."""

    provider = "ollama"
    enabled = True

    def __init__(
        self,
        model: str = "qwen:7b",
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 180.0,
        temperature: float = 0.2,
        num_ctx: int = 8192,
        num_predict: int = 4096,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        # Accept legacy full generate URL from configs.
        if self.base_url.endswith("/api/generate"):
            self.base_url = self.base_url[: -len("/api/generate")]
        self.timeout = timeout
        self.temperature = temperature
        # Ollama 默认 num_ctx=2048，装不下「长草稿 + 中英双语输出」，
        # 会导致输出被截断（例如只剩英文段），因此显式抬高上下文与输出上限。
        self.num_ctx = max(2048, int(num_ctx))
        self.num_predict = int(num_predict)

    def _options(self) -> dict[str, Any]:
        return {
            "temperature": self.temperature,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }

    def generate(self, prompt: str, system: str | None = None) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": self._options(),
        }
        # Qwen3: prefer non-thinking answers for manuscript polish.
        payload["think"] = False

        try:
            data = self._post_json(f"{self.base_url}/api/chat", payload)
            message = data.get("message") or {}
            content = message.get("content") or data.get("response") or ""
            return _strip_thinking(str(content))
        except Exception:
            # Older Ollama builds: /api/generate
            gen_payload = {
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": self._options(),
            }
            if system:
                gen_payload["system"] = system
            data = self._post_json(f"{self.base_url}/api/generate", gen_payload)
            return _strip_thinking(str(data.get("response") or ""))

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama HTTP {error.code}: {detail[:400]}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"Ollama unreachable at {url}: {error}") from error
        return json.loads(raw)
