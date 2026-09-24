from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


REGISTRY_VERSION = "1.0.0"


@dataclass(frozen=True)
class InputPort:
    id: str
    accepted_data_types: tuple[str, ...]
    accepted_scopes: tuple[str, ...] = ("sample",)
    required: bool = True
    multiple: bool = False
    collect: bool = False


@dataclass(frozen=True)
class OutputPort:
    id: str
    data_type: str
    scope: str = "sample"


@dataclass(frozen=True)
class NodeDefinition:
    type: str
    version: str
    label: str
    category: str
    nextflow_module: str | None
    process_name: str | None
    inputs: tuple[InputPort, ...] = ()
    outputs: tuple[OutputPort, ...] = ()
    parameters_schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    default_cpus: int = 1
    default_memory_gb: int = 2
    database_requirements: tuple[str, ...] = ()

    def input(self, port_id: str) -> InputPort | None:
        return next((port for port in self.inputs if port.id == port_id), None)

    def output(self, port_id: str) -> OutputPort | None:
        return next((port for port in self.outputs if port.id == port_id), None)


NodeRegistry = dict[str, NodeDefinition]


READ_TYPES = (
    "paired_raw_reads",
    "paired_clean_reads",
    "paired_host_removed_reads",
)

THREADS_SCHEMA = {
    "threads": {
        "type": "integer",
        "minimum": 1,
        "maximum": 256,
        "default": 4,
    }
}


def default_node_registry() -> NodeRegistry:
    definitions = (
        NodeDefinition(
            type="fastq_input",
            version="1.0.0",
            label="FASTQ 输入",
            category="input",
            nextflow_module=None,
            process_name=None,
            outputs=(OutputPort("reads", "paired_raw_reads"),),
        ),
        NodeDefinition(
            type="fastqc",
            version="1.0.0",
            label="FastQC 质量评估",
            category="qc",
            nextflow_module="workflow/modules/fastqc.nf",
            process_name="FASTQC_RAW",
            inputs=(InputPort("reads", READ_TYPES),),
            outputs=(OutputPort("report", "qc_report"),),
            parameters_schema=THREADS_SCHEMA,
            default_cpus=4,
            default_memory_gb=4,
        ),
        NodeDefinition(
            type="fastp",
            version="1.0.0",
            label="fastp 清洗",
            category="preprocessing",
            nextflow_module="workflow/modules/fastp.nf",
            process_name="FASTP",
            inputs=(InputPort("reads", ("paired_raw_reads",)),),
            outputs=(
                OutputPort("reads", "paired_clean_reads"),
                OutputPort("metrics", "fastp_metrics"),
            ),
            parameters_schema={
                **THREADS_SCHEMA,
                "qualified_quality_phred": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 93,
                    "default": 20,
                },
                "length_required": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 50,
                },
            },
            default_cpus=4,
            default_memory_gb=8,
        ),
        NodeDefinition(
            type="host_depletion",
            version="1.0.0",
            label="去除宿主序列",
            category="preprocessing",
            nextflow_module="workflow/modules/host_depletion.nf",
            process_name="HOST_DEPLETION",
            inputs=(
                InputPort(
                    "reads", ("paired_raw_reads", "paired_clean_reads")
                ),
            ),
            outputs=(
                OutputPort("reads", "paired_host_removed_reads"),
                OutputPort("metrics", "host_depletion_metrics"),
            ),
            parameters_schema={
                **THREADS_SCHEMA,
                "filter_mode": {
                    "type": "string",
                    "enum": ["strict_both_unmapped", "concordant_unmapped"],
                    "default": "strict_both_unmapped",
                },
            },
            default_cpus=4,
            default_memory_gb=8,
            database_requirements=("host.grch38_bowtie2",),
        ),
        NodeDefinition(
            type="taxonomy",
            version="1.0.0",
            label="物种注释",
            category="annotation",
            nextflow_module="workflow/modules/taxonomy.nf",
            process_name="TAXONOMY",
            inputs=(InputPort("reads", READ_TYPES),),
            outputs=(
                OutputPort("abundance", "taxonomy_abundance"),
                OutputPort("report", "taxonomy_report"),
            ),
            parameters_schema={
                **THREADS_SCHEMA,
                "read_length": {
                    "type": "integer",
                    "enum": [50, 75, 100, 150, 200, 250, 300],
                    "default": 150,
                },
            },
            default_cpus=4,
            default_memory_gb=16,
            database_requirements=(
                "taxonomy_reads.kraken2",
                "taxonomy_reads.bracken",
            ),
        ),
        NodeDefinition(
            type="functional_annotation",
            version="1.0.0",
            label="功能注释",
            category="annotation",
            nextflow_module="workflow/modules/functional_annotation.nf",
            process_name="FUNCTIONAL_ANNOTATION",
            inputs=(InputPort("reads", READ_TYPES),),
            outputs=(
                OutputPort("functions", "functional_abundance"),
                OutputPort("report", "functional_report"),
            ),
            parameters_schema=THREADS_SCHEMA,
            default_cpus=4,
            default_memory_gb=16,
            database_requirements=(
                "function_reads.humann_nucleotide",
                "function_reads.humann_protein",
                "function_reads.humann_utility",
                "function_reads.metaphlan",
            ),
        ),
        NodeDefinition(
            type="assembly",
            version="1.0.0",
            label="联合组装",
            category="mag",
            nextflow_module="workflow/modules/assembly.nf",
            process_name="ASSEMBLY",
            inputs=(
                InputPort(
                    "reads",
                    ("paired_clean_reads", "paired_host_removed_reads"),
                    collect=True,
                ),
            ),
            outputs=(OutputPort("assembly", "assembly_fasta", "cohort"),),
            parameters_schema={
                "threads": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 256,
                    "default": 8,
                },
                "memory_gb": {
                    "type": "integer",
                    "minimum": 4,
                    "default": 32,
                },
                "assembler": {
                    "type": "string",
                    "enum": ["megahit", "metaspades"],
                    "default": "megahit",
                },
            },
            default_cpus=8,
            default_memory_gb=32,
        ),
        NodeDefinition(
            type="binning",
            version="1.0.0",
            label="Binning",
            category="mag",
            nextflow_module="workflow/modules/binning.nf",
            process_name="BINNING",
            inputs=(
                InputPort(
                    "assembly", ("assembly_fasta",), ("cohort",)
                ),
            ),
            outputs=(OutputPort("bins", "mag_bins", "cohort"),),
            default_cpus=8,
            default_memory_gb=32,
        ),
        NodeDefinition(
            type="bin_refinement",
            version="1.0.0",
            label="Bin refinement",
            category="mag",
            nextflow_module="workflow/modules/bin_refinement.nf",
            process_name="BIN_REFINEMENT",
            inputs=(InputPort("bins", ("mag_bins",), ("cohort",)),),
            outputs=(OutputPort("bins", "refined_mag_bins", "cohort"),),
            parameters_schema={
                "completeness": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 70,
                },
                "contamination": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 5,
                },
            },
            default_cpus=8,
            default_memory_gb=32,
        ),
        NodeDefinition(
            type="bin_quantification",
            version="1.0.0",
            label="Bin quantification",
            category="mag",
            nextflow_module="workflow/modules/bin_quantification.nf",
            process_name="BIN_QUANTIFICATION",
            inputs=(
                InputPort("bins", ("refined_mag_bins",), ("cohort",)),
                InputPort(
                    "reads",
                    ("paired_clean_reads", "paired_host_removed_reads"),
                    collect=True,
                ),
            ),
            outputs=(
                OutputPort("abundance", "sample_mag_abundance", "cohort"),
            ),
            default_cpus=8,
            default_memory_gb=32,
        ),
        NodeDefinition(
            type="bin_reassembly",
            version="1.0.0",
            label="候选 Bin 重组装",
            category="mag",
            nextflow_module="workflow/modules/bin_reassembly.nf",
            process_name="BIN_REASSEMBLY",
            inputs=(InputPort("bins", ("refined_mag_bins",), ("cohort",)),),
            outputs=(OutputPort("mags", "mag_fasta", "cohort"),),
            default_cpus=8,
            default_memory_gb=32,
        ),
        NodeDefinition(
            type="bin_annotation",
            version="1.0.0",
            label="MAG 注释",
            category="mag",
            nextflow_module="workflow/modules/bin_annotation.nf",
            process_name="BIN_ANNOTATION",
            inputs=(
                InputPort(
                    "mags",
                    ("refined_mag_bins", "mag_fasta"),
                    ("cohort",),
                ),
            ),
            outputs=(
                OutputPort("taxonomy", "mag_taxonomy", "cohort"),
                OutputPort("functions", "mag_functions", "cohort"),
            ),
            default_cpus=8,
            default_memory_gb=32,
            database_requirements=(
                "mag_annotation.classification",
                "mag_annotation.function",
            ),
        ),
        NodeDefinition(
            type="report",
            version="1.0.0",
            label="汇总报告",
            category="report",
            nextflow_module="workflow/modules/report.nf",
            process_name="REPORT",
            inputs=(
                InputPort(
                    "artifacts",
                    (
                        "qc_report",
                        "fastp_metrics",
                        "host_depletion_metrics",
                        "taxonomy_abundance",
                        "taxonomy_report",
                        "functional_abundance",
                        "functional_report",
                        "sample_mag_abundance",
                        "mag_taxonomy",
                        "mag_functions",
                    ),
                    ("sample", "cohort"),
                    multiple=True,
                ),
            ),
            outputs=(OutputPort("report", "analysis_report", "cohort"),),
        ),
    )
    return {definition.type: definition for definition in definitions}


def serialize_node_registry(registry: NodeRegistry) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for definition in registry.values():
        nodes.append(
            {
                "type": definition.type,
                "version": definition.version,
                "label": definition.label,
                "category": definition.category,
                "inputs": [
                    {
                        "id": port.id,
                        "accepted_data_types": list(port.accepted_data_types),
                        "accepted_scopes": list(port.accepted_scopes),
                        "required": port.required,
                        "multiple": port.multiple,
                        "collect": port.collect,
                    }
                    for port in definition.inputs
                ],
                "outputs": [
                    {
                        "id": port.id,
                        "data_type": port.data_type,
                        "scope": port.scope,
                    }
                    for port in definition.outputs
                ],
                "parameters_schema": definition.parameters_schema,
                "resources": {
                    "default_cpus": definition.default_cpus,
                    "default_memory_gb": definition.default_memory_gb,
                },
                "database_requirements": list(
                    definition.database_requirements
                ),
            }
        )
    return {"registry_version": REGISTRY_VERSION, "nodes": nodes}
