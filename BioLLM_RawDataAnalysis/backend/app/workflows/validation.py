from __future__ import annotations

from collections import deque
from typing import Literal

from pydantic import BaseModel, Field

from .graph import WorkflowEdge, WorkflowGraph
from .registry import NodeRegistry


class GraphValidationIssue(BaseModel):
    severity: Literal["hard_error", "warning"]
    code: str
    message: str
    node_ids: list[str] = Field(default_factory=list)
    edge_id: str | None = None
    confirmation_key: str | None = None
    confirmed: bool = False


class GraphValidationResult(BaseModel):
    structurally_valid: bool
    can_execute: bool
    topological_order: list[str]
    issues: list[GraphValidationIssue]

    @property
    def hard_errors(self) -> list[GraphValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "hard_error"]

    @property
    def warnings(self) -> list[GraphValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def unconfirmed_warnings(self) -> list[GraphValidationIssue]:
        return [issue for issue in self.warnings if not issue.confirmed]


def _hard_error(
    code: str,
    message: str,
    *,
    node_ids: list[str] | None = None,
    edge_id: str | None = None,
) -> GraphValidationIssue:
    return GraphValidationIssue(
        severity="hard_error",
        code=code,
        message=message,
        node_ids=node_ids or [],
        edge_id=edge_id,
    )


def _warning(
    code: str,
    message: str,
    *,
    node_id: str,
    accepted_risks: set[str],
    edge_id: str | None = None,
) -> GraphValidationIssue:
    key = f"risk:{node_id}:{code}"
    return GraphValidationIssue(
        severity="warning",
        code=code,
        message=message,
        node_ids=[node_id],
        edge_id=edge_id,
        confirmation_key=key,
        confirmed=key in accepted_risks,
    )


def _topological_order(
    node_ids: list[str], edges: list[WorkflowEdge]
) -> tuple[list[str], bool]:
    known = set(node_ids)
    incoming = {node_id: 0 for node_id in node_ids}
    outgoing = {node_id: [] for node_id in node_ids}
    for edge in edges:
        if edge.source_node not in known or edge.target_node not in known:
            continue
        outgoing[edge.source_node].append(edge.target_node)
        incoming[edge.target_node] += 1
    ready = deque(node_id for node_id in node_ids if incoming[node_id] == 0)
    ordered: list[str] = []
    while ready:
        current = ready.popleft()
        ordered.append(current)
        for target in outgoing[current]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    return ordered, len(ordered) != len(node_ids)


def validate_workflow_graph(
    graph: WorkflowGraph, registry: NodeRegistry
) -> GraphValidationResult:
    issues: list[GraphValidationIssue] = []
    accepted_risks = set(graph.accepted_risks)
    nodes_by_id = {node.id: node for node in graph.nodes}

    if len(nodes_by_id) != len(graph.nodes):
        issues.append(
            _hard_error(
                "duplicate_node_id", "workflow node IDs must be unique"
            )
        )
    edge_ids = [edge.id for edge in graph.edges]
    if len(set(edge_ids)) != len(edge_ids):
        issues.append(
            _hard_error(
                "duplicate_edge_id", "workflow edge IDs must be unique"
            )
        )

    for node in graph.nodes:
        if node.type not in registry:
            issues.append(
                _hard_error(
                    "unknown_node_type",
                    f"unknown workflow node type: {node.type}",
                    node_ids=[node.id],
                )
            )

    connected_inputs: set[tuple[str, str]] = set()
    connection_counts: dict[tuple[str, str], int] = {}
    incoming_data_types: dict[tuple[str, str], str] = {}
    for edge in graph.edges:
        source = nodes_by_id.get(edge.source_node)
        target = nodes_by_id.get(edge.target_node)
        if source is None or target is None:
            issues.append(
                _hard_error(
                    "unknown_edge_node",
                    "edge references a node that is not present",
                    node_ids=[edge.source_node, edge.target_node],
                    edge_id=edge.id,
                )
            )
            continue
        source_definition = registry.get(source.type)
        target_definition = registry.get(target.type)
        if source_definition is None or target_definition is None:
            continue
        output_port = source_definition.output(edge.source_port)
        input_port = target_definition.input(edge.target_port)
        if output_port is None:
            issues.append(
                _hard_error(
                    "unknown_source_port",
                    f"node {source.id} has no output port {edge.source_port}",
                    node_ids=[source.id],
                    edge_id=edge.id,
                )
            )
            continue
        if input_port is None:
            issues.append(
                _hard_error(
                    "unknown_target_port",
                    f"node {target.id} has no input port {edge.target_port}",
                    node_ids=[target.id],
                    edge_id=edge.id,
                )
            )
            continue

        input_key = (target.id, input_port.id)
        connected_inputs.add(input_key)
        connection_counts[input_key] = connection_counts.get(input_key, 0) + 1
        incoming_data_types[input_key] = output_port.data_type
        if output_port.data_type not in input_port.accepted_data_types:
            issues.append(
                _hard_error(
                    "incompatible_port_types",
                    f"{output_port.data_type} cannot connect to {target.id}.{input_port.id}",
                    node_ids=[source.id, target.id],
                    edge_id=edge.id,
                )
            )
        if output_port.scope not in input_port.accepted_scopes:
            issues.append(
                _hard_error(
                    "incompatible_data_scope",
                    f"{output_port.scope} output cannot connect to {target.id}.{input_port.id}",
                    node_ids=[source.id, target.id],
                    edge_id=edge.id,
                )
            )
        if connection_counts[input_key] > 1 and not input_port.multiple:
            issues.append(
                _hard_error(
                    "multiple_connections_to_single_input",
                    f"{target.id}.{input_port.id} accepts only one connection",
                    node_ids=[target.id],
                    edge_id=edge.id,
                )
            )

    for node in graph.nodes:
        definition = registry.get(node.type)
        if definition is None:
            continue
        for input_port in definition.inputs:
            if input_port.required and (node.id, input_port.id) not in connected_inputs:
                issues.append(
                    _hard_error(
                        "missing_required_input",
                        f"{node.id}.{input_port.id} requires an upstream connection",
                        node_ids=[node.id],
                    )
                )

    topological_order, has_cycle = _topological_order(
        [node.id for node in graph.nodes], graph.edges
    )
    if has_cycle:
        issues.append(
            _hard_error("cycle_detected", "workflow graph must not contain cycles")
        )

    for node in graph.nodes:
        definition = registry.get(node.type)
        if definition is None:
            continue
        read_type = incoming_data_types.get((node.id, "reads"))
        incoming_edge = next(
            (
                edge
                for edge in graph.edges
                if edge.target_node == node.id and edge.target_port == "reads"
            ),
            None,
        )
        if node.type == "host_depletion" and read_type == "paired_raw_reads":
            issues.append(
                _warning(
                    "host_depletion_without_fastp",
                    "host depletion is receiving raw reads without fastp cleaning",
                    node_id=node.id,
                    accepted_risks=accepted_risks,
                    edge_id=incoming_edge.id if incoming_edge else None,
                )
            )
        if node.type in {"taxonomy", "functional_annotation"} and read_type in {
            "paired_raw_reads",
            "paired_clean_reads",
        }:
            issues.append(
                _warning(
                    "annotation_without_host_depletion",
                    "annotation is running without host-depleted reads",
                    node_id=node.id,
                    accepted_risks=accepted_risks,
                    edge_id=incoming_edge.id if incoming_edge else None,
                )
            )

    hard_errors = [issue for issue in issues if issue.severity == "hard_error"]
    unconfirmed = [
        issue
        for issue in issues
        if issue.severity == "warning" and not issue.confirmed
    ]
    structurally_valid = not hard_errors
    return GraphValidationResult(
        structurally_valid=structurally_valid,
        can_execute=structurally_valid and not unconfirmed,
        topological_order=topological_order,
        issues=issues,
    )
