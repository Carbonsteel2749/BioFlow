from __future__ import annotations

from bioflow.core.context import RunContext
from bioflow.core.models import ArtifactManifest, ModuleInput, ModuleOutput, ModuleSpec, ModuleStatus
from bioflow.modules.base import Module
from bioflow.modules.catalog import register


@register
class AbstractModule(Module):
    spec = ModuleSpec(
        name="abstract",
        kind="writing",
        deps=["methods", "results", "discussion"],
        description="Generate abstract draft.",
    )

    def run(self, context: RunContext, module_input: ModuleInput) -> ModuleOutput:
        evidence_count = sum(len(dep.evidence) for dep in module_input.deps.values())
        summary = f"Abstract draft generated with {evidence_count} evidence items."
        if context.model_router and module_input.payload.get("use_llm"):
            prompt = "Write a concise abstract based on available evidence."
            summary = context.model_router.generate(prompt)

        artifact_dir = context.artifacts_dir / "abstract"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifacts = ArtifactManifest(root=str(artifact_dir))
        return ModuleOutput(
            module=self.spec.name,
            status=ModuleStatus.succeeded,
            summary=summary,
            artifacts=artifacts,
        )
