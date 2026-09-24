from __future__ import annotations

from bioflow.core.context import RunContext
from bioflow.core.models import ArtifactManifest, ModuleInput, ModuleOutput, ModuleSpec, ModuleStatus
from bioflow.modules.base import Module
from bioflow.modules.catalog import register


@register
class ResultsModule(Module):
    spec = ModuleSpec(
        name="results",
        kind="writing",
        deps=["result_analysis"],
        description="Generate results section draft.",
    )

    def run(self, context: RunContext, module_input: ModuleInput) -> ModuleOutput:
        analysis_output = module_input.deps.get("result_analysis")
        evidence_count = len(analysis_output.evidence) if analysis_output else 0
        summary = f"Results draft generated with {evidence_count} evidence items."
        if context.model_router and module_input.payload.get("use_llm"):
            prompt = "Write results section grounded in evidence summaries."
            summary = context.model_router.generate(prompt)

        artifact_dir = context.artifacts_dir / "results"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifacts = ArtifactManifest(root=str(artifact_dir))
        return ModuleOutput(
            module=self.spec.name,
            status=ModuleStatus.succeeded,
            summary=summary,
            artifacts=artifacts,
            evidence=analysis_output.evidence if analysis_output else [],
        )
