"""Build LLM clients from config / CLI flags."""

from __future__ import annotations

from article_writing.llm.base import FakeLLM, LLMClient, LLMDisabled
from article_writing.llm.ollama import OllamaClient
from article_writing.llm.openai_compat import OpenAICompatClient


def build_llm_client(
    *,
    enabled: bool = False,
    provider: str = "ollama",
    model: str = "qwen:7b",
    url: str = "http://127.0.0.1:11434",
    api_key: str | None = None,
    timeout: float = 180.0,
    temperature: float = 0.2,
) -> LLMClient:
    """Return an LLM client. Default is disabled (template-only pipeline)."""

    if not enabled:
        return LLMDisabled()

    name = (provider or "ollama").strip().lower()
    if name in {"disabled", "none", "off"}:
        return LLMDisabled()
    if name == "fake":
        return FakeLLM()
    if name == "ollama":
        return OllamaClient(
            model=model or "qwen:7b",
            base_url=url or "http://127.0.0.1:11434",
            timeout=timeout,
            temperature=temperature,
        )
    if name in {"openai", "openai_compat", "deepseek", "vllm"}:
        # Sensible defaults when switching provider without overriding URL/model.
        default_url = url or "https://api.openai.com/v1"
        default_model = model or "gpt-4o-mini"
        if name == "deepseek":
            default_url = url or "https://api.deepseek.com/v1"
            default_model = model or "deepseek-chat"
        return OpenAICompatClient(
            model=default_model,
            base_url=default_url,
            api_key=api_key,
            timeout=timeout,
            temperature=temperature,
        )
    raise ValueError(
        f"Unknown LLM provider {provider!r}; use ollama|openai|openai_compat|deepseek|vllm|fake"
    )
