from __future__ import annotations

import base64
import hashlib
import heapq
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .graph import WorkflowGraph
from .parameters import ParameterValidationError, validate_parameter_values
from .registry import REGISTRY_VERSION, NodeRegistry, standard_task_parameters_schema
from .validation import GraphValidationResult, validate_workflow_graph


COMPILER_VERSION = "1.1.0"


class WorkflowCompilationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        validation: GraphValidationResult | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.validation = validation


@dataclass(frozen=True)
class CompiledWorkflow:
    source: str
    nextflow_config: str
    graph_hash: str
    registry_version: str
    compiler_version: str
    topological_order: tuple[str, ...]
    node_aliases: dict[str, str]
    node_parameters: dict[str, dict[str, object]]
    nextflow_parameters: dict[str, object]
    node_output_channels: dict[str, dict[str, str]]
    node_method_versions: dict[str, str] = field(default_factory=dict)
    node_runtime_parameters: dict[str, dict[str, object]] = field(default_factory=dict)
    node_output_roots: dict[str, str] = field(default_factory=dict)
    node_fingerprints: dict[str, str] = field(default_factory=dict)


_OUTPUT_EMITS = {
    "fastqc": {"report": "reports"},
    "fastp": {"reads": "reads", "metrics": "reports"},
    "host_depletion": {"reads": "reads", "metrics": "metrics"},
    "taxonomy": {"abundance": "reports", "report": "reports"},
    "functional_annotation": {
        "functions": "reports",
        "report": "reports",
    },
    "assembly": {"assembly": "mag_inputs"},
    "binning": {"bins": "bins"},
    "bin_refinement": {"bins": "refined"},
    "bin_quantification": {"abundance": "abundance"},
    "bin_reassembly": {"mags": "reassembled"},
    "bin_annotation": {
        "taxonomy": "mag_taxonomy",
        "functions": "mag_functions",
    },
    "report": {"report": "archive"},
}

_DATABASE_MANIFEST_INPUTS = {
    "taxonomy",
    "functional_annotation",
    "bin_quantification",
    "bin_annotation",
    "report",
}

def _canonical_graph_payload(
    graph: WorkflowGraph,
    node_parameters: dict[str, dict[str, object]],
    registry: NodeRegistry,
) -> dict[str, object]:
    return {
        "schema_version": graph.schema_version,
        "nodes": [
            {
                "id": node.id,
                "type": node.type,
                "parameters": node_parameters[node.id],
                "method_version": registry[node.type].method_version or registry[node.type].version,
            }
            for node in sorted(graph.nodes, key=lambda item: item.id)
        ],
        "edges": [
            {
                "source_node": edge.source_node,
                "source_port": edge.source_port,
                "target_node": edge.target_node,
                "target_port": edge.target_port,
            }
            for edge in sorted(
                graph.edges,
                key=lambda item: (
                    item.source_node,
                    item.source_port,
                    item.target_node,
                    item.target_port,
                    item.id,
                ),
            )
        ],
        "registry_version": REGISTRY_VERSION,
        "compiler_version": COMPILER_VERSION,
    }


def _graph_hash(
    graph: WorkflowGraph,
    node_parameters: dict[str, dict[str, object]],
    registry: NodeRegistry,
) -> str:
    payload = json.dumps(
        _canonical_graph_payload(graph, node_parameters, registry),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _node_alias(node_type: str, node_id: str) -> str:
    safe_type = re.sub(r"[^A-Z0-9]+", "_", node_type.upper()).strip("_")
    suffix = hashlib.sha256(f"{node_type}\0{node_id}".encode()).hexdigest()[:10]
    return f"WF_{safe_type}_{suffix.upper()}"


def _module_path(project_root: Path, relative_path: str) -> Path:
    root = project_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkflowCompilationError(
            "module_outside_project",
            f"registered module escapes the project root: {relative_path}",
        ) from exc
    if not candidate.is_file():
        raise WorkflowCompilationError(
            "module_not_found",
            f"registered Nextflow module does not exist: {relative_path}",
        )
    return candidate


def _channel_name(node_id: str, port_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", node_id).strip("_").lower()
    port = re.sub(r"[^A-Za-z0-9]+", "_", port_id).strip("_").lower()
    return f"node_{safe}_{port}"


def _deterministic_topological_order(graph: WorkflowGraph) -> tuple[str, ...]:
    node_ids = {node.id for node in graph.nodes}
    incoming = {node_id: 0 for node_id in node_ids}
    outgoing = {node_id: set() for node_id in node_ids}
    for edge in graph.edges:
        if edge.source_node not in node_ids or edge.target_node not in node_ids:
            continue
        if edge.target_node not in outgoing[edge.source_node]:
            outgoing[edge.source_node].add(edge.target_node)
            incoming[edge.target_node] += 1
    ready = [node_id for node_id, count in incoming.items() if count == 0]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        node_id = heapq.heappop(ready)
        ordered.append(node_id)
        for target in sorted(outgoing[node_id]):
            incoming[target] -= 1
            if incoming[target] == 0:
                heapq.heappush(ready, target)
    return tuple(ordered)


def _normalized_node_parameters(
    graph: WorkflowGraph,
    registry: NodeRegistry,
    validation: GraphValidationResult,
) -> dict[str, dict[str, object]]:
    normalized: dict[str, dict[str, object]] = {}
    for node in graph.nodes:
        definition = registry[node.type]
        try:
            normalized[node.id] = validate_parameter_values(
                definition.parameters_schema,
                node.parameters,
                owner=f"node {node.id}",
            )
        except ParameterValidationError as exc:
            raise WorkflowCompilationError(
                "invalid_node_parameters",
                str(exc),
                validation=validation,
            ) from exc
    return normalized


def _node_method_versions(
    graph: WorkflowGraph, registry: NodeRegistry
) -> dict[str, str]:
    versions: dict[str, str] = {}
    for node in graph.nodes:
        definition = registry[node.type]
        versions[node.id] = definition.method_version or definition.version
    return versions

def _render_nextflow_config(
    topological_order: tuple[str, ...],
    nodes_by_id: dict[str, object],
    registry: NodeRegistry,
    node_aliases: dict[str, str],
    node_parameters: dict[str, dict[str, object]],
    project_root: Path,
    node_runtime_parameters: dict[str, dict[str, object]],
    node_output_roots: dict[str, str],
    node_fingerprints: dict[str, str],
) -> str:
    base_config = _module_path(project_root, "workflow/nextflow.config")
    lines = [f"includeConfig '{base_config.as_posix()}'", "", "process {"]
    for node_id in topological_order:
        node = nodes_by_id[node_id]
        if node.type == "fastq_input":
            continue
        definition = registry[node.type]
        parameters = node_parameters[node_id]
        cpus = parameters.get("threads", definition.default_cpus)
        memory_gb = parameters.get("memory_gb", definition.default_memory_gb)
        encoded = base64.b64encode(json.dumps(
            node_runtime_parameters[node_id], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).decode("ascii")
        lines.extend(
            [
                f"    withName: '{node_aliases[node_id]}' {{",
                f"        cpus = {cpus}",
                f"        memory = '{memory_gb} GB'",
                f"        ext.bioflow_parameters = new groovy.json.JsonSlurper().parseText(new String('{encoded}'.decodeBase64(), 'UTF-8'))",
                f"        ext.bioflow_output_subdir = '{node_output_roots[node_id]}'",
                f"        ext.bioflow_fingerprint = '{node_fingerprints[node_id]}'",
                "    }",
            ]
        )
    lines.extend(["}", ""])
    return "\n".join(lines)


def _map_nextflow_parameters(
    topological_order: tuple[str, ...],
    nodes_by_id: dict[str, object],
    node_parameters: dict[str, dict[str, object]],
    validation: GraphValidationResult,
    registry: NodeRegistry,
) -> dict[str, object]:
    mapped: dict[str, object] = {}
    conflicts: set[str] = set()
    for node_id in topological_order:
        node = nodes_by_id[node_id]
        definition = registry[node.type]
        bindings = {
            name: schema["nextflow_parameter"]
            for name, schema in definition.parameters_schema.items()
            if isinstance(schema.get("nextflow_parameter"), str)
        }
        for node_parameter, nextflow_parameter in bindings.items():
            if node_parameter not in node_parameters[node_id]:
                continue
            value = node_parameters[node_id][node_parameter]
            if (
                nextflow_parameter in mapped
                and mapped[nextflow_parameter] != value
            ):
                conflicts.add(nextflow_parameter)
            mapped[nextflow_parameter] = value
    # Dynamic sources do not import main.nf's defaults. Preserve the same
    # script parameters in the immutable snapshot, then apply node overrides.
    defaults = {name: definition['default'] for name, definition in
                standard_task_parameters_schema().items() if 'default' in definition}
    # Preserve legacy flat values only when unambiguous. Dynamic processes use
    # their exact alias-scoped map, never these globals for scientific options.
    defaults.update({name: value for name, value in mapped.items() if name not in conflicts})
    return {name: defaults[name] for name in sorted(defaults)}


def _node_runtime_parameters(graph, registry, normalized):
    standard = standard_task_parameters_schema()
    result = {}
    for node in graph.nodes:
        if node.type == "fastq_input":
            continue
        values = {}
        for name, schema in registry[node.type].parameters_schema.items():
            binding = schema.get("nextflow_parameter")
            if not isinstance(binding, str):
                continue
            # Inactive options still need a safe CLI value (e.g. fastp's sliding
            # window settings when both cuts are disabled), but cannot inherit
            # a sibling node's value through the legacy global parameter map.
            if name in normalized[node.id]:
                values[binding] = normalized[node.id][name]
            elif "default" in schema:
                values[binding] = schema["default"]
            elif "default" in standard.get(binding, {}):
                values[binding] = standard[binding]["default"]
        if node.type == "bin_reassembly":
            for name in ("mag_memory_gb", "bin_completeness", "bin_contamination"):
                values.setdefault(name, standard[name]["default"])
        result[node.id] = values
    return result


def compile_workflow(
    graph: WorkflowGraph,
    registry: NodeRegistry,
    *,
    project_root: Path,
) -> CompiledWorkflow:
    validation = validate_workflow_graph(graph, registry)
    if not validation.structurally_valid:
        raise WorkflowCompilationError(
            "invalid_workflow_graph",
            "workflow graph contains non-overridable validation errors",
            validation=validation,
        )
    if validation.unconfirmed_warnings:
        raise WorkflowCompilationError(
            "unconfirmed_workflow_risk",
            "workflow graph contains risks that require confirmation",
            validation=validation,
        )

    node_parameters = _normalized_node_parameters(graph, registry, validation)
    node_method_versions = _node_method_versions(graph, registry)

    nodes_by_id = {node.id: node for node in graph.nodes}
    topological_order = _deterministic_topological_order(graph)
    nextflow_parameters = _map_nextflow_parameters(
        topological_order,
        nodes_by_id,
        node_parameters,
        validation,
        registry,
    )
    input_nodes = [node for node in graph.nodes if node.type == "fastq_input"]
    if len(input_nodes) != 1:
        raise WorkflowCompilationError(
            "invalid_input_node_count",
            "V1 workflows require exactly one FASTQ input node",
            validation=validation,
        )

    _module_path(project_root, "workflow/modules/validate.nf")
    node_aliases = {
        node.id: _node_alias(node.type, node.id)
        for node in graph.nodes
        if node.type != "fastq_input"
    }
    node_runtime_parameters = _node_runtime_parameters(graph, registry, node_parameters)
    node_output_roots = {node_id: f"nodes/{alias}" for node_id, alias in node_aliases.items()}
    node_fingerprints = {}
    for node_id in node_aliases:
        definition = registry[nodes_by_id[node_id].type]
        payload = {
            "parameters": node_parameters[node_id],
            "runtime_parameters": node_runtime_parameters[node_id],
            "method_version": node_method_versions[node_id],
            "cpus": node_parameters[node_id].get("threads", definition.default_cpus),
            "memory_gb": node_parameters[node_id].get("memory_gb", definition.default_memory_gb),
        }
        node_fingerprints[node_id] = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
    # REPORT serializes params into its run provenance. Flat legacy bindings
    # alone cannot describe two same-type nodes with different settings.
    nextflow_parameters["bioflow_nodes"] = {
        node_id: {
            "type": nodes_by_id[node_id].type,
            "alias": node_aliases[node_id],
            "parameters": node_parameters[node_id],
            "runtime_parameters": node_runtime_parameters[node_id],
            "method_version": node_method_versions[node_id],
            "output_root": node_output_roots[node_id],
        }
        for node_id in sorted(node_aliases)
    }

    include_lines = [
        "include { VALIDATE_MANIFEST as WF_VALIDATE_MANIFEST } "
        "from './workflow/modules/validate.nf'"
    ]
    for node_id in topological_order:
        node = nodes_by_id[node_id]
        if node.type == "fastq_input":
            continue
        definition = registry[node.type]
        if definition.nextflow_module is None or definition.process_name is None:
            raise WorkflowCompilationError(
                "node_is_not_compilable",
                f"node type has no Nextflow execution contract: {node.type}",
                validation=validation,
            )
        if node.type not in _OUTPUT_EMITS:
            raise WorkflowCompilationError(
                "unsupported_node_type",
                f"node type is not supported by this compiler phase: {node.type}",
                validation=validation,
            )
        _module_path(project_root, definition.nextflow_module)
        include_lines.append(
            f"include {{ {definition.process_name} as {node_aliases[node.id]} }} "
            f"from './{Path(definition.nextflow_module).as_posix()}'"
        )

    input_node = input_nodes[0]
    input_reads_channel = _channel_name(input_node.id, "reads")
    workflow_lines = [
        "workflow {",
        "    manifest_ch = Channel.value(file(params.input_manifest, checkIfExists: true))",
        "    WF_VALIDATE_MANIFEST(manifest_ch)",
        f"    {input_reads_channel} = WF_VALIDATE_MANIFEST.out.validated_manifest",
        "        .splitCsv(header: true)",
        "        .map { row -> tuple(row.sample_id, file(row.read1), file(row.read2)) }",
    ]
    output_channels = {(input_node.id, "reads"): input_reads_channel}
    node_output_channels: dict[str, dict[str, str]] = {
        input_node.id: {"reads": input_reads_channel}
    }
    for node_id in topological_order:
        node = nodes_by_id[node_id]
        if node.type == "fastq_input":
            continue
        definition = registry[node.type]
        incoming_by_port = {
            edge.target_port: edge
            for edge in graph.edges
            if edge.target_node == node.id
        }
        if node.type == "report":
            artifact_edges = sorted(
                (
                    edge
                    for edge in graph.edges
                    if edge.target_node == node.id
                    and edge.target_port == "artifacts"
                ),
                key=lambda edge: (
                    edge.source_node,
                    edge.source_port,
                    edge.target_port,
                ),
            )
            artifact_channels = [
                output_channels[(edge.source_node, edge.source_port)]
                for edge in artifact_edges
            ]
            mixed_expression = artifact_channels[0] + "".join(
                f".mix({channel})" for channel in artifact_channels[1:]
            )
            artifacts_channel = _channel_name(node.id, "artifacts")
            workflow_lines.extend(
                [
                    f"    {artifacts_channel} = {mixed_expression}",
                    "        .map { value -> value instanceof List && value && value[0] instanceof String ? value.drop(1) : value }",
                    "        .flatten()",
                    "        .collect()",
                    "    report_parameters_base64 = groovy.json.JsonOutput.toJson(params).bytes.encodeBase64().toString()",
                ]
            )
            arguments = [
                artifacts_channel,
                "database_manifest_ch",
                "params.input_manifest.toString()",
                "report_parameters_base64",
                "params.run_name ?: params.task_id",
                'params.nextflow_work_root ?: "${projectDir}/work"',
            ]
        elif node.type == "assembly":
            incoming = incoming_by_port["reads"]
            source_channel = output_channels[
                (incoming.source_node, incoming.source_port)
            ]
            collected_channel = _channel_name(node.id, "reads")
            workflow_lines.extend(
                [
                    f"    {collected_channel} = {source_channel}",
                    "        .map { sample_id, read1, read2 -> [read1, read2] }",
                    "        .flatten()",
                    "        .collect()",
                ]
            )
            arguments = [collected_channel]
        elif node.type == "bin_quantification":
            incoming = incoming_by_port["bins"]
            bins_channel = output_channels[
                (incoming.source_node, incoming.source_port)
            ]
            arguments = [
                bins_channel,
                "manifest_ch",
                "database_manifest_ch",
            ]
        elif node.type == "bin_annotation":
            incoming = incoming_by_port["mags"]
            source_channel = output_channels[
                (incoming.source_node, incoming.source_port)
            ]
            cohort_channel = _channel_name(node.id, "mags")
            workflow_lines.extend(
                [
                    f"    {cohort_channel} = {source_channel}",
                    "        .map { bins, assembly, reads -> tuple(params.cohort_id ?: 'coassembly', bins) }",
                ]
            )
            arguments = [cohort_channel, "database_manifest_ch"]
        else:
            arguments = []
            for input_port in definition.inputs:
                incoming = incoming_by_port[input_port.id]
                arguments.append(
                    output_channels[
                        (incoming.source_node, incoming.source_port)
                    ]
                )
            if node.type in _DATABASE_MANIFEST_INPUTS:
                arguments.append("database_manifest_ch")
        workflow_lines.append(
            f"    {node_aliases[node.id]}({', '.join(arguments)})"
        )
        node_output_channels[node.id] = {}
        for port_id, emit_name in _OUTPUT_EMITS[node.type].items():
            channel = f"{node_aliases[node.id]}.out.{emit_name}"
            output_channels[(node.id, port_id)] = channel
            node_output_channels[node.id][port_id] = channel
    workflow_lines.append("}")

    if any(
        node.type in _DATABASE_MANIFEST_INPUTS
        for node in graph.nodes
        if node.type != "fastq_input"
    ):
        workflow_lines.insert(
            2,
            "    database_manifest_ch = Channel.value(file(params.database_manifest, checkIfExists: true))",
        )

    source = "\n".join(
        [
            "nextflow.enable.dsl=2",
            "",
            *include_lines,
            "",
            *workflow_lines,
            "",
        ]
    )
    nextflow_config = _render_nextflow_config(
        topological_order,
        nodes_by_id,
        registry,
        node_aliases,
        node_parameters,
        project_root,
        node_runtime_parameters,
        node_output_roots,
        node_fingerprints,
    )
    return CompiledWorkflow(
        source=source,
        nextflow_config=nextflow_config,
        graph_hash=_graph_hash(graph, node_parameters, registry),
        registry_version=REGISTRY_VERSION,
        compiler_version=COMPILER_VERSION,
        topological_order=topological_order,
        node_aliases=node_aliases,
        node_parameters=node_parameters,
        node_method_versions=node_method_versions,
        nextflow_parameters=nextflow_parameters,
        node_output_channels=node_output_channels,
        node_runtime_parameters=node_runtime_parameters,
        node_output_roots=node_output_roots,
        node_fingerprints=node_fingerprints,
    )
