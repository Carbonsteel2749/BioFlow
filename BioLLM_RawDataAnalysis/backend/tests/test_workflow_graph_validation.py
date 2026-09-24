from backend.app.workflows.graph import WorkflowEdge, WorkflowGraph, WorkflowNode
from backend.app.workflows.registry import (
    default_node_registry,
    serialize_node_registry,
)
from backend.app.workflows.validation import validate_workflow_graph


def node(node_id: str, node_type: str) -> WorkflowNode:
    return WorkflowNode(id=node_id, type=node_type, parameters={})


def edge(
    edge_id: str,
    source: str,
    source_port: str,
    target: str,
    target_port: str,
) -> WorkflowEdge:
    return WorkflowEdge(
        id=edge_id,
        source_node=source,
        source_port=source_port,
        target_node=target,
        target_port=target_port,
    )


def validate(
    nodes: list[WorkflowNode],
    edges: list[WorkflowEdge],
    accepted_risks: list[str] | None = None,
):
    graph = WorkflowGraph(
        schema_version="1.0",
        nodes=nodes,
        edges=edges,
        accepted_risks=accepted_risks or [],
    )
    return validate_workflow_graph(graph, default_node_registry())


def test_fastqc_can_run_as_a_valid_partial_workflow():
    result = validate(
        [node("input", "fastq_input"), node("qc", "fastqc")],
        [edge("e1", "input", "reads", "qc", "reads")],
    )

    assert result.structurally_valid is True
    assert result.can_execute is True
    assert result.topological_order == ["input", "qc"]
    assert result.issues == []


def test_incompatible_ports_are_a_non_overridable_hard_error():
    result = validate(
        [
            node("input", "fastq_input"),
            node("qc", "fastqc"),
            node("trim", "fastp"),
        ],
        [
            edge("e1", "input", "reads", "qc", "reads"),
            edge("e2", "qc", "report", "trim", "reads"),
        ],
    )

    assert result.structurally_valid is False
    assert result.can_execute is False
    assert [issue.code for issue in result.hard_errors] == [
        "incompatible_port_types"
    ]
    assert result.hard_errors[0].edge_id == "e2"
    assert result.hard_errors[0].confirmation_key is None


def test_raw_reads_directly_to_host_depletion_require_confirmation():
    nodes = [node("input", "fastq_input"), node("host", "host_depletion")]
    edges = [edge("e1", "input", "reads", "host", "reads")]

    first = validate(nodes, edges)

    assert first.structurally_valid is True
    assert first.can_execute is False
    assert [issue.code for issue in first.warnings] == [
        "host_depletion_without_fastp"
    ]
    confirmation_key = first.warnings[0].confirmation_key
    assert confirmation_key == "risk:host:host_depletion_without_fastp"

    confirmed = validate(nodes, edges, accepted_risks=[confirmation_key])
    assert confirmed.structurally_valid is True
    assert confirmed.can_execute is True
    assert confirmed.unconfirmed_warnings == []
    assert confirmed.warnings[0].confirmed is True


def test_cycles_are_reported_even_when_an_edge_types_are_also_invalid():
    result = validate(
        [node("host", "host_depletion")],
        [edge("loop", "host", "reads", "host", "reads")],
    )

    assert result.structurally_valid is False
    assert "cycle_detected" in {issue.code for issue in result.hard_errors}


def test_taxonomy_and_functional_annotation_form_valid_parallel_branches():
    result = validate(
        [
            node("input", "fastq_input"),
            node("trim", "fastp"),
            node("host", "host_depletion"),
            node("taxonomy", "taxonomy"),
            node("function", "functional_annotation"),
        ],
        [
            edge("e1", "input", "reads", "trim", "reads"),
            edge("e2", "trim", "reads", "host", "reads"),
            edge("e3", "host", "reads", "taxonomy", "reads"),
            edge("e4", "host", "reads", "function", "reads"),
        ],
    )

    assert result.can_execute is True
    assert result.topological_order[:3] == ["input", "trim", "host"]
    assert set(result.topological_order[3:]) == {"taxonomy", "function"}


def test_missing_required_input_is_a_hard_error():
    result = validate([node("qc", "fastqc")], [])

    assert result.structurally_valid is False
    assert [issue.code for issue in result.hard_errors] == [
        "missing_required_input"
    ]
    assert result.hard_errors[0].node_ids == ["qc"]


def test_node_registry_catalog_contains_frontend_and_execution_contracts():
    catalog = serialize_node_registry(default_node_registry())

    assert catalog["registry_version"] == "1.0.0"
    definitions = {item["type"]: item for item in catalog["nodes"]}
    assert set(definitions) == {
        "fastq_input",
        "fastqc",
        "fastp",
        "host_depletion",
        "taxonomy",
        "functional_annotation",
        "assembly",
        "binning",
        "bin_refinement",
        "bin_quantification",
        "bin_reassembly",
        "bin_annotation",
        "report",
    }
    assert definitions["fastp"]["parameters_schema"]["threads"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 256,
        "default": 4,
    }
    assert definitions["fastp"]["resources"] == {
        "default_cpus": 4,
        "default_memory_gb": 8,
    }
    assert definitions["taxonomy"]["database_requirements"] == [
        "taxonomy_reads.kraken2",
        "taxonomy_reads.bracken",
    ]
    assert definitions["assembly"]["inputs"][0]["collect"] is True
