from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, Iterable, List, Optional, Set

from bioflow.core.context import RunContext
from bioflow.core.models import ArtifactManifest, ModuleInput, ModuleOutput, ModuleStatus
from bioflow.core.registry import ModuleRegistry
from bioflow.orchestrator.state import ModuleRunRecord, RunReport


class ModuleScheduler:
    def __init__(self, registry: ModuleRegistry) -> None:
        self.registry = registry

    def _dependency_closure(self, module_names: Iterable[str]) -> Set[str]:
        required: Set[str] = set()

        def visit(name: str) -> None:
            if name in required:
                return
            if not self.registry.has(name):
                raise ValueError(f"unknown module: {name}")
            required.add(name)
            for dep in self.registry.get_spec(name).deps:
                visit(dep)

        for module_name in module_names:
            visit(module_name)
        return required

    def _toposort(self, module_names: Iterable[str]) -> List[str]:
        deps_map: Dict[str, Set[str]] = {}
        reverse_map: Dict[str, Set[str]] = defaultdict(set)
        for name in module_names:
            deps = set(self.registry.get_spec(name).deps)
            deps_map[name] = deps
            for dep in deps:
                reverse_map[dep].add(name)

        queue = deque([name for name, deps in deps_map.items() if not deps])
        ordered: List[str] = []

        while queue:
            node = queue.popleft()
            ordered.append(node)
            for child in reverse_map.get(node, set()):
                deps_map[child].discard(node)
                if not deps_map[child]:
                    queue.append(child)

        if len(ordered) != len(deps_map):
            raise ValueError("dependency cycle detected")
        return ordered

    def run(
        self,
        context: RunContext,
        module_names: Optional[List[str]] = None,
        inputs: Optional[Dict[str, ModuleInput]] = None,
        fail_fast: bool = False,
    ) -> RunReport:
        if not module_names:
            module_names = [spec.name for spec in self.registry.list_specs()]

        inputs = inputs or {}
        required = self._dependency_closure(module_names)
        ordered = self._toposort(required)

        report = RunReport()
        outputs: Dict[str, ModuleOutput] = {}

        for name in ordered:
            report.records[name] = ModuleRunRecord(module=name)

        for name in ordered:
            record = report.records[name]
            spec = self.registry.get_spec(name)

            if any(outputs.get(dep, None) is None or outputs[dep].status != ModuleStatus.succeeded for dep in spec.deps):
                record.mark_finished(ModuleStatus.blocked, error="dependency failed")
                outputs[name] = ModuleOutput(
                    module=name,
                    status=ModuleStatus.blocked,
                    artifacts=ArtifactManifest(root=str(context.artifacts_dir)),
                    summary="",
                )
                continue

            record.mark_started()
            module_input = inputs.get(
                name,
                ModuleInput(run_id=context.run_id, module=name, payload={}),
            )
            module_input.deps = {dep: outputs[dep] for dep in spec.deps if dep in outputs}

            try:
                module = self.registry.create(name)
                result = module.run(context, module_input)
                if result.status == ModuleStatus.pending:
                    result.status = ModuleStatus.succeeded
                outputs[name] = result
                context.module_outputs[name] = result
                record.mark_finished(result.status)
            except Exception as exc:  # noqa: BLE001
                record.mark_finished(ModuleStatus.failed, error=str(exc))
                outputs[name] = ModuleOutput(
                    module=name,
                    status=ModuleStatus.failed,
                    artifacts=ArtifactManifest(root=str(context.artifacts_dir)),
                    summary="",
                    warnings=[str(exc)],
                )
                context.module_outputs[name] = outputs[name]
                if fail_fast:
                    break

        if fail_fast:
            for name in ordered:
                if report.records[name].status == ModuleStatus.pending:
                    report.records[name].mark_finished(ModuleStatus.skipped, error="fail_fast")

        return report
