from __future__ import annotations

from bioflow.core.context import RunContext
from bioflow.core.models import ArtifactManifest, ModuleInput, ModuleOutput, ModuleSpec, ModuleStatus


class Module:
    spec: ModuleSpec

    def run(self, context: RunContext, module_input: ModuleInput) -> ModuleOutput:
        raise NotImplementedError

    def _default_output(self, context: RunContext, status: ModuleStatus) -> ModuleOutput:
        return ModuleOutput(
            module=self.spec.name,
            status=status,
            summary="",
            artifacts=ArtifactManifest(root=str(context.artifacts_dir)),
        )
