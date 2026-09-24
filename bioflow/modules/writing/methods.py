from __future__ import annotations

from bioflow.core.context import RunContext
from bioflow.core.models import ArtifactManifest, ModuleInput, ModuleOutput, ModuleSpec, ModuleStatus
from bioflow.modules.base import Module
from bioflow.modules.catalog import register


@register
class MethodsModule(Module):
    spec = ModuleSpec(
        name="methods",
        kind="writing",
        deps=["data_processing"],
        description="Generate methods section draft.",
    )

    def run(self, context: RunContext, module_input: ModuleInput) -> ModuleOutput:
        data_output = module_input.deps.get("data_processing")
        summary = "Methods draft generated."
        if data_output and data_output.metrics:
            summary = f"Methods draft generated from {len(data_output.metrics)} metrics."
        if context.model_router and module_input.payload.get("use_llm"):
            prompt = "Write methods based on the processing pipeline and key parameters."
            summary = context.model_router.generate(prompt)

        artifact_dir = context.artifacts_dir / "methods"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifacts = ArtifactManifest(root=str(artifact_dir))
        return ModuleOutput(
            module=self.spec.name,
            status=ModuleStatus.succeeded,
            summary=summary,
            artifacts=artifacts,
        )
