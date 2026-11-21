"""Ollama HTTP client (local models; default BioFLow recommendation)."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# One chapter may keep generating after the first tokens arrive. The configured
# timeout is the longest silence, not a cap on a chapter that is still writing.
_MIN_CHAPTER_BUDGET = 1200.0


def _strip_thinking(text: str) -> str:
    cleaned = _THINK_RE.sub("", text or "")
    return cleaned.strip()


class OllamaClient:
    """Talks to Ollama ``/api/chat`` (preferred) with ``/api/generate`` fallback."""

    provider = "ollama"
    enabled = True

    def __init__(
        self,
        model: str = "qwen3:14b",
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
            "stream": True,
            "options": self._options(),
            # Keep the 14B weights loaded across the seven chapters.
            "keep_alive": "30m",
        }
        # Qwen3: prefer non-thinking answers for manuscript polish.
        payload["think"] = False
        chat_url = f"{self.base_url}/api/chat"
        try:
            return self._collect_stream(chat_url, payload)
        except urllib.error.HTTPError as error:
            code = error.code
            if code == 400 and "think" in payload:
                payload = {key: value for key, value in payload.items() if key != "think"}
                return self._collect_stream(chat_url, payload)
            if code not in (404, 405):
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Ollama HTTP {code}: {detail[:400]}") from error
        # Older Ollama builds have no /api/chat.
        gen_payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": True,
            "keep_alive": "30m",
            "options": self._options(),
        }
        if system:
            gen_payload["system"] = system
        return self._collect_stream(f"{self.base_url}/api/generate", gen_payload)

    def _collect_stream(self, url: str, payload: dict[str, Any]) -> str:
        """Read a streamed reply. Silence uses ``timeout``; writing may continue longer."""

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        pieces: list[str] = []
        started = time.monotonic()
        budget = max(float(self.timeout), _MIN_CHAPTER_BUDGET)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                while True:
                    if time.monotonic() - started > budget:
                        raise TimeoutError(f"model still writing after {int(budget)}s")
                    line = response.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    try:
                        event = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    message = event.get("message") if isinstance(event, dict) else None
                    piece = ""
                    if isinstance(message, dict):
                        piece = str(message.get("content") or "")
                    if not piece and isinstance(event, dict):
                        piece = str(event.get("response") or "")
                    if piece:
                        pieces.append(piece)
                    if isinstance(event, dict) and event.get("done"):
                        break
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as error:
            if _is_timeout(error):
                raise TimeoutError(f"model sent no text for {int(self.timeout)}s") from error
            raise RuntimeError(f"Ollama unreachable at {url}: {error}") from error
        except TimeoutError as error:
            if str(error).startswith("model "):
                raise
            raise TimeoutError(f"model sent no text for {int(self.timeout)}s") from error
        text = _strip_thinking("".join(pieces))
        if not text:
            raise RuntimeError("Ollama returned an empty section")
        return text


def _is_timeout(error: BaseException) -> bool:
    reason = getattr(error, "reason", error)
    return isinstance(reason, TimeoutError) or "timed out" in str(reason).lower()
