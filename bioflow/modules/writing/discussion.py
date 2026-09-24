from __future__ import annotations

from bioflow.core.context import RunContext
from bioflow.core.models import ArtifactManifest, ModuleInput, ModuleOutput, ModuleSpec, ModuleStatus
from bioflow.modules.base import Module
from bioflow.modules.catalog import register


@register
class DiscussionModule(Module):
    spec = ModuleSpec(
        name="discussion",
        kind="writing",
        deps=["results", "introduction"],
        description="Generate discussion section draft.",
    )

    def run(self, context: RunContext, module_input: ModuleInput) -> ModuleOutput:
        results_output = module_input.deps.get("results")
        evidence_count = len(results_output.evidence) if results_output else 0
        summary = f"Discussion draft generated with {evidence_count} evidence items."
        if context.model_router and module_input.payload.get("use_llm"):
            prompt = "Write discussion with cautious tone and link to literature."
            summary = context.model_router.generate(prompt)

        artifact_dir = context.artifacts_dir / "discussion"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifacts = ArtifactManifest(root=str(artifact_dir))
        return ModuleOutput(
            module=self.spec.name,
            status=ModuleStatus.succeeded,
            summary=summary,
            artifacts=artifacts,
            evidence=results_output.evidence if results_output else [],
        )
