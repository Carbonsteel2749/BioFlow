from __future__ import annotations

import copy
from typing import Any

from ..workflows.registry import NodeRegistry


# These templates deliberately end at their native outputs: the existing
# aggregate report node requires additional analysis databases.
BASIC_TEMPLATE_SPECS = (
    (
        "builtin-data-quality",
        "数据质量检查",
        "使用 FastQC 评估双端原始 reads 的质量、碱基组成和接头信号。",
        (("input", "fastq_input"), ("raw_qc", "fastqc")),
        (("input", "raw_qc"),),
        ("每个样本 R1/R2 的 FastQC HTML 报告", "FastQC ZIP 原始质量统计"),
    ),
    (
        "builtin-qc-host",
        "质控与去宿主",
        "使用 fastp 清洗双端 reads，再通过 Bowtie2 去除宿主序列。",
        (("input", "fastq_input"), ("trim", "fastp"), ("host", "host_depletion")),
        (("input", "trim"), ("trim", "host")),
        ("fastp 清洗后双端 FASTQ", "fastp HTML/JSON 报告",
         "去宿主双端 FASTQ", "宿主去除 metrics JSON"),
    ),
    (
        "builtin-species-composition",
        "物种组成分析",
        "清洗、去宿主后使用 Kraken2 分类及 Bracken 估算物种组成；不含功能注释。",
        (("input", "fastq_input"), ("trim", "fastp"),
         ("host", "host_depletion"), ("taxonomy", "taxonomy")),
        (("input", "trim"), ("trim", "host"), ("host", "taxonomy")),
        ("fastp 与去宿主产物", "Kraken 分类结果与报告",
         "Bracken species 表", "species_abundance.tsv",
         "taxonomy.validation.json", "taxonomy.provenance.json"),
    ),
    (
        'builtin-qc-clean', '清洗与质控',
        'FastQC 检查原始短读长双端数据，再用 fastp 清洗；不执行去宿主或数据库注释。',
        (('input','fastq_input'), ('raw_qc','fastqc'), ('trim','fastp')),
        (('input','raw_qc'), ('input','trim')),
        ('原始 FastQC HTML/ZIP', '清洗后双端 FASTQ', 'fastp HTML/JSON 质量统计'),
    ),
    (
        'builtin-functional-potential', '功能潜力分析',
        '清洗与去宿主后，用 HUMAnN 描述群落总体功能潜力；不执行 Kraken2 物种分析或 MAG 分析。',
        (('input','fastq_input'), ('trim','fastp'), ('host','host_depletion'), ('function','functional_annotation')),
        (('input','trim'), ('trim','host'), ('host','function')),
        ('基因家族、KO、EC、通路丰度与覆盖度标准表', 'HUMAnN 原始输出', 'functional.provenance.json'),
    ),
)


def basic_template_records(registry: NodeRegistry) -> list[dict[str, Any]]:
    """Describe basic workflows using only defaults from the shared registry."""
    records = []
    for template_id, name, description, node_specs, connections, outputs in BASIC_TEMPLATE_SPECS:
        nodes = [
            {
                "id": node_id,
                "type": node_type,
                "parameters": {
                    key: copy.deepcopy(rule["default"])
                    for key, rule in registry[node_type].parameters_schema.items()
                    if "default" in rule
                },
            }
            for node_id, node_type in node_specs
        ]
        records.append({
            "template_id": template_id,
            "name": name,
            "description": description,
            "input_requirements": [
                "双端 FASTQ（R1/R2），通过现有上传入口生成样本清单",
                "清单字段：sample_id、read1、read2；同一样本的 reads 必须配对",
            ],
            "expected_outputs": list(outputs),
            "workflow": {
                "schema_version": "1.0",
                "nodes": nodes,
                "edges": [
                    {
                        "id": f"e{index:02d}",
                        "source_node": source,
                        "source_port": "reads",
                        "target_node": target,
                        "target_port": "reads",
                    }
                    for index, (source, target) in enumerate(connections, start=1)
                ],
                "accepted_risks": [],
            },
        })
    return records
