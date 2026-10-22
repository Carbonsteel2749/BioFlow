"""LLM client protocol for optional section polishing."""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Minimal text-generation interface used by the writing plate."""

    provider: str
    model: str
    enabled: bool

    def generate(self, prompt: str, system: str | None = None) -> str:
        """Return model text. Implementations must not stream."""


class LLMDisabled:
    """Default client: polishing is a no-op / hard fail if called."""

    provider = "disabled"
    model = "none"
    enabled = False

    def generate(self, prompt: str, system: str | None = None) -> str:
        del prompt, system
        raise RuntimeError("LLM disabled; sections use templates only")


class FakeLLM:
    """Deterministic client for unit tests (no network)."""

    provider = "fake"
    model = "fake-echo"
    enabled = True

    def __init__(self, marker: str = "[llm-polished]") -> None:
        self.marker = marker

    def generate(self, prompt: str, system: str | None = None) -> str:
        del system
        if "```markdown" in prompt:
            start = prompt.rfind("```markdown") + len("```markdown")
            end = prompt.find("```", start)
            body = prompt[start:end].strip() if end > start else prompt.strip()
        else:
            body = prompt.strip()
        # Drop existing H1 so bilingual wrapper is clean.
        lines = body.splitlines()
        if lines and lines[0].startswith("# "):
            title = lines[0][2:].strip()
            body = "\n".join(lines[1:]).strip()
        else:
            title = "Section"
        return (
            f"# {title}\n\n"
            f"## English\n\n"
            f"> {self.marker}\n\n"
            f"{body}\n\n"
            f"## 中文\n\n"
            f"> {self.marker}\n\n"
            f"{body}\n"
        )
