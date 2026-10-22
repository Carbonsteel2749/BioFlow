"""OpenAI-compatible chat completions (DeepSeek, vLLM, Ollama /v1, etc.)."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class OpenAICompatClient:
    """POST ``{base_url}/chat/completions`` with Bearer auth when a key is set."""

    provider = "openai_compat"
    enabled = True

    def __init__(
        self,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        timeout: float = 180.0,
        temperature: float = 0.2,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/chat/completions"):
            self.base_url = self.base_url[: -len("/chat/completions")]
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.timeout = timeout
        self.temperature = temperature

    def generate(self, prompt: str, system: str | None = None) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/chat/completions"
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI-compat HTTP {error.code}: {detail[:400]}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"OpenAI-compat unreachable at {url}: {error}") from error

        data = json.loads(raw)
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenAI-compat response had no choices")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        return _THINK_RE.sub("", str(content)).strip()
