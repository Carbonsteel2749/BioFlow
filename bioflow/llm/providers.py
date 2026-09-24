from __future__ import annotations

from typing import Dict, Optional

import requests


class LLMProvider:
    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        raise NotImplementedError


class OllamaProvider(LLMProvider):
    def __init__(self, url: str, model: str) -> None:
        self.url = url
        self.model = model

    def generate(self, prompt: str, system: Optional[str] = None, **kwargs) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system or "",
            "stream": False,
        }
        if "temperature" in kwargs:
            payload["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            payload["options"] = {"num_predict": kwargs["max_tokens"]}
        response = requests.post(self.url, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        return str(data.get("response", "")).strip()


class OpenAIProvider(LLMProvider):
    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError("OpenAI provider not configured")
