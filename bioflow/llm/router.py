from __future__ import annotations

from typing import Dict, Optional

from bioflow.core.models import ModelSpec, RoutePolicy
from bioflow.evidence.sanitization import SanitizationRules, sanitize_text
from bioflow.llm.providers import LLMProvider


class ModelRouter:
    def __init__(
        self,
        providers: Dict[str, LLMProvider],
        default_provider: str,
        policy: Optional[RoutePolicy] = None,
        sanitization: Optional[SanitizationRules] = None,
    ) -> None:
        self.providers = providers
        self.default_provider = default_provider
        self.policy = policy or RoutePolicy()
        self.sanitization = sanitization

    def select(self, model: Optional[ModelSpec] = None) -> LLMProvider:
        if model and model.provider in self.providers:
            return self.providers[model.provider]
        return self.providers[self.default_provider]

    def generate(self, prompt: str, system: Optional[str] = None, model: Optional[ModelSpec] = None) -> str:
        provider = self.select(model)
        if self.sanitization:
            prompt = sanitize_text(prompt, self.sanitization).text
            if system:
                system = sanitize_text(system, self.sanitization).text
        return provider.generate(
            prompt,
            system=system,
            temperature=model.temperature if model else 0.2,
            max_tokens=model.max_tokens if model else 1024,
        )
