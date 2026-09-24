import shutil
import subprocess
from pathlib import Path

import pytest

from backend.app.workflows.compiler import (
    WorkflowCompilationError,
    compile_workflow,
)
from backend.app.workflows.graph import (
    CanvasPosition,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
)
from backend.app.workflows.materializer import materialize_compilation
from backend.app.workflows.registry import default_node_registry


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_fastqc_partial_workflow_compiles_only_required_allowlisted_modules():
    graph = WorkflowGraph(
        schema_version="1.0",
        nodes=[
            WorkflowNode(id="input", type="fastq_input"),
            WorkflowNode(id="qc", type="fastqc"),
        ],
        edges=[
            WorkflowEdge(
                id="input-to-qc",
                source_node="input",
                source_port="reads",
                target_node="qc",
                target_port="reads",
            )
        ],
    )

    compiled = compile_workflow(
        graph,
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )

    assert compiled.topological_order == ("input", "qc")
    assert set(compiled.node_aliases) == {"qc"}
    assert "VALIDATE_MANIFEST as WF_VALIDATE_MANIFEST" in compiled.source
    assert "FASTQC_RAW as " in compiled.source
    assert "workflow/modules/fastqc.nf" in compiled.source
    assert "workflow/modules/fastp.nf" not in compiled.source
    assert "WF_VALIDATE_MANIFEST(manifest_ch)" in compiled.source
    assert f"{compiled.node_aliases['qc']}(node_input_reads)" in compiled.source


def test_compiled_config_inherits_project_nextflow_defaults():
    graph = WorkflowGraph(
        schema_version="1.0",
        nodes=[
            WorkflowNode(id="input", type="fastq_input"),
            WorkflowNode(id="qc", type="fastqc"),
        ],
        edges=[
            WorkflowEdge(
                id="input-to-qc",
                source_node="input",
                source_port="reads",
                target_node="qc",
                target_port="reads",
            )
        ],
    )

    compiled = compile_workflow(
        graph,
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )

    expected = (PROJECT_ROOT / "workflow" / "nextflow.config").as_posix()
    assert compiled.nextflow_config.startswith(f"includeConfig '{expected}'\n\n")


def _fastqc_graph(
    *,
    edge_id: str,
    input_position: CanvasPosition,
    qc_position: CanvasPosition,
    threads: int,
    reverse_nodes: bool = False,
) -> WorkflowGraph:
    nodes = [
        WorkflowNode(
            id="input",
            type="fastq_input",
            position=input_position,
        ),
        WorkflowNode(
            id="qc",
            type="fastqc",
            parameters={"threads": threads},
            position=qc_position,
        ),
    ]
    if reverse_nodes:
        nodes.reverse()
    return WorkflowGraph(
        schema_version="1.0",
        nodes=nodes,
        edges=[
            WorkflowEdge(
                id=edge_id,
                source_node="input",
                source_port="reads",
                target_node="qc",
                target_port="reads",
            )
        ],
    )


def test_compilation_identity_ignores_canvas_metadata_but_tracks_parameters():
    first = compile_workflow(
        _fastqc_graph(
            edge_id="edge-one",
            input_position=CanvasPosition(x=10, y=20),
            qc_position=CanvasPosition(x=300, y=20),
            threads=4,
        ),
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )
    rearranged = compile_workflow(
        _fastqc_graph(
            edge_id="edge-renamed-by-ui",
            input_position=CanvasPosition(x=900, y=500),
            qc_position=CanvasPosition(x=100, y=40),
            threads=4,
            reverse_nodes=True,
        ),
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )
    changed_parameters = compile_workflow(
        _fastqc_graph(
            edge_id="edge-one",
            input_position=CanvasPosition(x=10, y=20),
            qc_position=CanvasPosition(x=300, y=20),
            threads=8,
        ),
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )
    omitted_defaults = compile_workflow(
        WorkflowGraph(
            schema_version="1.0",
            nodes=[
                WorkflowNode(id="input", type="fastq_input"),
                WorkflowNode(id="qc", type="fastqc"),
            ],
            edges=[
                WorkflowEdge(
                    id="input-to-qc",
                    source_node="input",
                    source_port="reads",
                    target_node="qc",
                    target_port="reads",
                )
            ],
        ),
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )

    assert rearranged.graph_hash == first.graph_hash
    assert rearranged.source == first.source
    assert omitted_defaults.graph_hash == first.graph_hash
    assert omitted_defaults.node_parameters["qc"] == {"threads": 4}
    assert f"withName: '{first.node_aliases['qc']}'" in first.nextflow_config
    assert "cpus = 4" in first.nextflow_config
    assert "memory = '4 GB'" in first.nextflow_config
    assert changed_parameters.graph_hash != first.graph_hash
    assert "cpus = 8" in changed_parameters.nextflow_config


def test_core_annotation_branches_share_host_removed_reads_and_database_manifest():
    graph = WorkflowGraph(
        schema_version="1.0",
        nodes=[
            WorkflowNode(id="input", type="fastq_input"),
            WorkflowNode(id="trim", type="fastp"),
            WorkflowNode(id="host", type="host_depletion"),
            WorkflowNode(id="taxonomy", type="taxonomy"),
            WorkflowNode(id="function", type="functional_annotation"),
        ],
        edges=[
            WorkflowEdge(
                id="e1",
                source_node="input",
                source_port="reads",
                target_node="trim",
                target_port="reads",
            ),
            WorkflowEdge(
                id="e2",
                source_node="trim",
                source_port="reads",
                target_node="host",
                target_port="reads",
            ),
            WorkflowEdge(
                id="e3",
                source_node="host",
                source_port="reads",
                target_node="taxonomy",
                target_port="reads",
            ),
            WorkflowEdge(
                id="e4",
                source_node="host",
                source_port="reads",
                target_node="function",
                target_port="reads",
            ),
        ],
    )

    compiled = compile_workflow(
        graph,
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )

    trim = compiled.node_aliases["trim"]
    host = compiled.node_aliases["host"]
    taxonomy = compiled.node_aliases["taxonomy"]
    function = compiled.node_aliases["function"]
    assert compiled.source.count("database_manifest_ch =") == 1
    assert f"{trim}(node_input_reads)" in compiled.source
    assert f"{host}({trim}.out.reads)" in compiled.source
    assert (
        f"{taxonomy}({host}.out.reads, database_manifest_ch)"
        in compiled.source
    )
    assert (
        f"{function}({host}.out.reads, database_manifest_ch)"
        in compiled.source
    )
    assert compiled.node_output_channels["host"]["reads"] == (
        f"{host}.out.reads"
    )
    assert compiled.node_output_channels["taxonomy"]["abundance"] == (
        f"{taxonomy}.out.reports"
    )


def test_mag_branch_collects_samples_and_preserves_cohort_contracts():
    graph = WorkflowGraph(
        schema_version="1.0",
        nodes=[
            WorkflowNode(id="input", type="fastq_input"),
            WorkflowNode(id="trim", type="fastp"),
            WorkflowNode(id="host", type="host_depletion"),
            WorkflowNode(id="assembly", type="assembly"),
            WorkflowNode(id="binning", type="binning"),
            WorkflowNode(id="refine", type="bin_refinement"),
            WorkflowNode(id="quant", type="bin_quantification"),
            WorkflowNode(id="reassembly", type="bin_reassembly"),
            WorkflowNode(id="annotation", type="bin_annotation"),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node="input", source_port="reads", target_node="trim", target_port="reads"),
            WorkflowEdge(id="e2", source_node="trim", source_port="reads", target_node="host", target_port="reads"),
            WorkflowEdge(id="e3", source_node="host", source_port="reads", target_node="assembly", target_port="reads"),
            WorkflowEdge(id="e4", source_node="assembly", source_port="assembly", target_node="binning", target_port="assembly"),
            WorkflowEdge(id="e5", source_node="binning", source_port="bins", target_node="refine", target_port="bins"),
            WorkflowEdge(id="e6", source_node="refine", source_port="bins", target_node="quant", target_port="bins"),
            WorkflowEdge(id="e7", source_node="host", source_port="reads", target_node="quant", target_port="reads"),
            WorkflowEdge(id="e8", source_node="refine", source_port="bins", target_node="reassembly", target_port="bins"),
            WorkflowEdge(id="e9", source_node="reassembly", source_port="mags", target_node="annotation", target_port="mags"),
        ],
    )

    compiled = compile_workflow(
        graph,
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )

    aliases = compiled.node_aliases
    assert "node_assembly_reads = " + aliases["host"] + ".out.reads" in compiled.source
    assert f"{aliases['assembly']}(node_assembly_reads)" in compiled.source
    assert f"{aliases['binning']}({aliases['assembly']}.out.mag_inputs)" in compiled.source
    assert f"{aliases['refine']}({aliases['binning']}.out.bins)" in compiled.source
    assert (
        f"{aliases['quant']}({aliases['refine']}.out.refined, manifest_ch, database_manifest_ch)"
        in compiled.source
    )
    assert f"{aliases['reassembly']}({aliases['refine']}.out.refined)" in compiled.source
    assert (
        f"node_annotation_mags = {aliases['reassembly']}.out.reassembled"
        in compiled.source
    )
    assert (
        f"{aliases['annotation']}(node_annotation_mags, database_manifest_ch)"
        in compiled.source
    )
    assert compiled.node_output_channels["quant"]["abundance"] == (
        f"{aliases['quant']}.out.abundance"
    )
    assert compiled.node_output_channels["annotation"]["taxonomy"] == (
        f"{aliases['annotation']}.out.mag_taxonomy"
    )


def test_report_node_collects_multiple_artifact_channels_into_basic_bundle():
    graph = WorkflowGraph(
        schema_version="1.0",
        nodes=[
            WorkflowNode(id="input", type="fastq_input"),
            WorkflowNode(id="qc", type="fastqc"),
            WorkflowNode(id="trim", type="fastp"),
            WorkflowNode(id="report", type="report"),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node="input", source_port="reads", target_node="qc", target_port="reads"),
            WorkflowEdge(id="e2", source_node="input", source_port="reads", target_node="trim", target_port="reads"),
            WorkflowEdge(id="e3", source_node="qc", source_port="report", target_node="report", target_port="artifacts"),
            WorkflowEdge(id="e4", source_node="trim", source_port="metrics", target_node="report", target_port="artifacts"),
        ],
    )

    compiled = compile_workflow(
        graph,
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )

    aliases = compiled.node_aliases
    assert (
        f"node_report_artifacts = {aliases['qc']}.out.reports.mix({aliases['trim']}.out.reports)"
        in compiled.source
    )
    assert "report_parameters_base64 = " in compiled.source
    assert (
        f"{aliases['report']}(node_report_artifacts, database_manifest_ch, "
        in compiled.source
    )
    assert compiled.node_output_channels["report"]["report"] == (
        f"{aliases['report']}.out.archive"
    )


def test_parallel_graph_compilation_is_independent_of_json_list_order():
    input_node = WorkflowNode(id="input", type="fastq_input")
    qc_node = WorkflowNode(id="qc", type="fastqc")
    trim_node = WorkflowNode(id="trim", type="fastp")
    qc_edge = WorkflowEdge(
        id="edge-qc",
        source_node="input",
        source_port="reads",
        target_node="qc",
        target_port="reads",
    )
    trim_edge = WorkflowEdge(
        id="edge-trim",
        source_node="input",
        source_port="reads",
        target_node="trim",
        target_port="reads",
    )
    first = WorkflowGraph(
        schema_version="1.0",
        nodes=[input_node, qc_node, trim_node],
        edges=[qc_edge, trim_edge],
    )
    reordered = WorkflowGraph(
        schema_version="1.0",
        nodes=[trim_node, qc_node, input_node],
        edges=[trim_edge, qc_edge],
    )

    first_compilation = compile_workflow(
        first,
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )
    reordered_compilation = compile_workflow(
        reordered,
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )

    assert reordered_compilation.graph_hash == first_compilation.graph_hash
    assert reordered_compilation.source == first_compilation.source
    assert reordered_compilation.topological_order == (
        "input",
        "qc",
        "trim",
    )


def test_generated_fastqc_workflow_is_accepted_by_nextflow_preview(tmp_path):
    compiled = compile_workflow(
        _fastqc_graph(
            edge_id="input-to-qc",
            input_position=CanvasPosition(x=10, y=20),
            qc_position=CanvasPosition(x=300, y=20),
            threads=1,
        ),
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )
    artifacts = materialize_compilation(
        compiled,
        task_id="compiler-preview",
        state_root=tmp_path,
        workflow_root=PROJECT_ROOT / "workflow",
    )
    manifest = (
        PROJECT_ROOT
        / "workflow/tests/fixtures/integration/manifests/samples.relative.csv"
    )
    nextflow = shutil.which("nextflow")
    assert nextflow is not None

    result = subprocess.run(
        [
            nextflow,
            "-c",
            str(artifacts.config_path),
            "run",
            str(artifacts.source_path),
            "-preview",
            "-ansi-log",
            "false",
            "--input_manifest",
            str(manifest),
            "--outdir",
            str(tmp_path / "output"),
            "--task_id",
            "compiler-preview",
            "--threads",
            "1",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "parameters",
    [
        {"threads": 0},
        {"threads": True},
        {"unregistered_shell": "echo unsafe"},
    ],
)
def test_compiler_rejects_unregistered_or_invalid_node_parameters(parameters):
    graph = WorkflowGraph(
        schema_version="1.0",
        nodes=[
            WorkflowNode(id="input", type="fastq_input"),
            WorkflowNode(id="qc", type="fastqc", parameters=parameters),
        ],
        edges=[
            WorkflowEdge(
                id="input-to-qc",
                source_node="input",
                source_port="reads",
                target_node="qc",
                target_port="reads",
            )
        ],
    )

    with pytest.raises(WorkflowCompilationError) as captured:
        compile_workflow(
            graph,
            default_node_registry(),
            project_root=PROJECT_ROOT,
        )

    assert captured.value.code == "invalid_node_parameters"
    assert "qc" in str(captured.value)


def test_registered_node_parameters_are_mapped_to_existing_nextflow_parameters():
    graph = WorkflowGraph(
        schema_version="1.0",
        nodes=[
            WorkflowNode(id="input", type="fastq_input"),
            WorkflowNode(
                id="trim",
                type="fastp",
                parameters={
                    "threads": 6,
                    "qualified_quality_phred": 30,
                    "length_required": 75,
                },
            ),
        ],
        edges=[
            WorkflowEdge(
                id="input-to-trim",
                source_node="input",
                source_port="reads",
                target_node="trim",
                target_port="reads",
            )
        ],
    )

    compiled = compile_workflow(
        graph,
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )

    assert compiled.nextflow_parameters == {
        "fastp_length_required": 75,
        "fastp_qualified_quality_phred": 30,
    }
    assert "cpus = 6" in compiled.nextflow_config


def test_compiler_requires_explicit_confirmation_for_allowed_risk():
    nodes = [
        WorkflowNode(id="input", type="fastq_input"),
        WorkflowNode(id="host", type="host_depletion"),
    ]
    edges = [
        WorkflowEdge(
            id="raw-to-host",
            source_node="input",
            source_port="reads",
            target_node="host",
            target_port="reads",
        )
    ]
    unconfirmed = WorkflowGraph(
        schema_version="1.0",
        nodes=nodes,
        edges=edges,
    )

    with pytest.raises(WorkflowCompilationError) as captured:
        compile_workflow(
            unconfirmed,
            default_node_registry(),
            project_root=PROJECT_ROOT,
        )
    assert captured.value.code == "unconfirmed_workflow_risk"

    confirmed = unconfirmed.model_copy(
        update={
            "accepted_risks": [
                "risk:host:host_depletion_without_fastp"
            ]
        }
    )
    compiled = compile_workflow(
        confirmed,
        default_node_registry(),
        project_root=PROJECT_ROOT,
    )
    assert (
        f"{compiled.node_aliases['host']}(node_input_reads)"
        in compiled.source
    )


def test_compiler_rejects_conflicting_global_module_parameters():
    graph = WorkflowGraph(
        schema_version="1.0",
        nodes=[
            WorkflowNode(id="input", type="fastq_input"),
            WorkflowNode(
                id="trim-a",
                type="fastp",
                parameters={"length_required": 50},
            ),
            WorkflowNode(
                id="trim-b",
                type="fastp",
                parameters={"length_required": 75},
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node="input", source_port="reads", target_node="trim-a", target_port="reads"),
            WorkflowEdge(id="e2", source_node="input", source_port="reads", target_node="trim-b", target_port="reads"),
        ],
    )

    with pytest.raises(WorkflowCompilationError) as captured:
        compile_workflow(
            graph,
            default_node_registry(),
            project_root=PROJECT_ROOT,
        )

    assert captured.value.code == "conflicting_runtime_parameter"
    assert "fastp_length_required" in str(captured.value)
