"""Optional LLM polishing for manuscript sections.

Default recommendation for BioFLow demos: local Ollama + ``qwen3:14b``
(already configured in ``configs/example.yaml``). Cloud OpenAI-compatible
APIs (DeepSeek / OpenAI / vLLM) are supported via ``provider=openai_compat``.
"""

from __future__ import annotations

from article_writing.llm.base import FakeLLM, LLMClient, LLMDisabled
from article_writing.llm.factory import build_llm_client
from article_writing.llm.ollama import OllamaClient
from article_writing.llm.openai_compat import OpenAICompatClient
from article_writing.llm.polish import (
    DEFAULT_POLISH_SECTIONS,
    DEFAULT_WRITING_REQUIREMENTS,
    polish_section_draft,
)

__all__ = [
    "DEFAULT_POLISH_SECTIONS",
    "DEFAULT_WRITING_REQUIREMENTS",
    "FakeLLM",
    "LLMClient",
    "LLMDisabled",
    "OllamaClient",
    "OpenAICompatClient",
    "build_llm_client",
    "polish_section_draft",
]
