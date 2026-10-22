from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


REGISTRY_VERSION = "2.1.0"

PARAMETER_DESCRIPTIONS = {
    '计算线程': '此节点允许使用的 CPU 线程数量，不是同时运行的任务数；提高线程会增加资源竞争。',
    'MAG 线程': 'MAG 计算步骤的 CPU 线程预算，不代表同时执行的样本数。',
    '内存': '此节点的 Nextflow 内存资源声明；实际是否强制限额需结合工具和执行器。',
    'MAG 内存': 'MAG 组装阶段的内存预算；过低可能失败，过高会影响其他任务。',
    '合格碱基质量': 'Phred 质量分数低于此值的碱基计为低质量；Q20 对应约 1% 碱基错误率。',
    'fastp 质量阈值': 'fastp 判断低质量碱基的 Phred 阈值；改变此值会改变保留的 reads。',
    '低质量碱基上限': '每条 read 允许的低质量碱基比例上限，超过后该 read 被过滤。',
    'N 碱基上限': '每条 read 最多允许的未知碱基 N 个数，超过上限将被过滤。',
    '最低保留长度': '清洗后 read 的最短保留长度，短于此值会被过滤。',
    '前端滑窗剪切': '从 read 前端按滑动窗口质量剪切低质量序列。',
    '尾端滑窗剪切': '从 read 尾端按滑动窗口质量剪切低质量序列。',
    '滑窗大小': '质量剪切窗口包含的碱基数；至少启用一端剪切时生效。',
    '滑窗平均质量': '滑动窗口平均 Phred 质量阈值；至少启用一端剪切时生效。',
    'PolyG 剪切': '剪切末端连续 G；常用于两色测序的 poly-G 尾部，需结合平台判断。',
    '双端纠错': '利用 paired-end reads 的重叠区纠正可识别的碱基错误。',
    '自动识别接头': '自动检测 paired-end 接头并剪切；关闭后须确认接头处理策略。',
    '宿主过滤模式': 'strict_both_unmapped 仅保留两端均未比对宿主的 pairs；另一模式按成对比对关系保留。',
    '最少保留 read pairs': '去宿主后保留 pairs 少于此值则停止并告警，用于样本质量保护。',
    '最大去除比例': '去宿主比例超过此阈值则暂停；单位为百分比，不是 0–1 小数。',
    'Bowtie2 灵敏度': '选择速度与敏感度的折中；更敏感的预设通常增加计算时间。',
    'Bracken 读长': '选择匹配测序读长的 Bracken 数据库，必须存在对应读长的数据库文件。',
    '启用 MAG 分析': '启用联合组装和 MAG 分支，需足够资源及通过预检的 MAG 数据库。',
    '候选 Bin 重组装': '对候选 Bin 启用现有重组装步骤，仅在 MAG 分支生效。',
    '组装器': '选择已适配的 MEGAHIT 或 metaSPAdes；不会自动更换数据库或 binning 算法。',
    '最低完整度': '保留 MAG 的完整度下限，由当前质量评估工具估计，单位为百分比。',
    '最高污染度': '保留 MAG 的污染度上限；过滤阈值不等同于完整 MIMAG 质量认证。',
    '宿主参考索引': '显式宿主参考索引；未指定时使用服务器管理配置，启动前必须校验。',
    'Kraken2 数据库': '显式数据库位置，必须与 registry profile、Bracken 和分类体系兼容。',
    'HUMAnN 核酸库': 'HUMAnN 核酸参考库，由数据库 profile 固定并校验关键文件。',
    'HUMAnN 蛋白库': 'HUMAnN 蛋白参考库，必须记录 release 和 manifest。',
    'MetaPhlAn 数据库': '与 HUMAnN 初步物种谱一致的 MetaPhlAn 标记基因参考库。',
    '方法版本快照': '记录本次方法版本，不是任意切换工具版本的开关。',
}


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
    method_id: str | None = None
    method_version: str | None = None
    tool_requirements: tuple[str, ...] = ()

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

def parameter_schema(
    parameter_type: str,
    *,
    default: Any | None = None,
    minimum: int | float | None = None,
    maximum: int | float | None = None,
    enum: tuple[Any, ...] | None = None,
    title: str | None = None,
    description: str | None = None,
    group: str = "scientific",
    unit: str | None = None,
    required: bool = False,
    active_when: dict[str, Any] | tuple[dict[str, Any], ...] | None = None,
    nextflow_parameter: str | None = None,
    nullable: bool = False,
    optional: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": parameter_type, "group": group}
    if optional:
        payload['optional'] = True
    if nullable:
        payload["nullable"] = True
    if default is not None:
        payload["default"] = default
    if minimum is not None:
        payload["minimum"] = minimum
    if maximum is not None:
        payload["maximum"] = maximum
    if enum is not None:
        payload["enum"] = list(enum)
    if title is not None:
        payload["title"] = title
    payload['description'] = description or PARAMETER_DESCRIPTIONS.get(title or '', f'{title or "参数"}：仅用于当前已注册方法；以执行适配器定义为准。')
    if unit is not None:
        payload["unit"] = unit
    if required:
        payload["required"] = True
    if active_when is not None:
        payload["active_when"] = list(active_when) if isinstance(active_when, tuple) else active_when
    if nextflow_parameter is not None:
        payload["nextflow_parameter"] = nextflow_parameter
    return payload


def threads_schema(default: int = 4) -> dict[str, dict[str, Any]]:
    return {
        "threads": parameter_schema(
            "integer",
            minimum=1,
            maximum=256,
            default=default,
            title="计算线程",
            group="resources",
            unit="threads",
        )
    }


def memory_schema(default: int, minimum: int = 4, *, nextflow_parameter: str | None = None) -> dict[str, dict[str, Any]]:
    return {
        "memory_gb": parameter_schema(
            "integer",
            minimum=minimum,
            default=default,
            title="内存",
            group="resources",
            unit="GB",
            nextflow_parameter=nextflow_parameter,
        )
    }


THREADS_SCHEMA = threads_schema(4)


def default_node_registry() -> NodeRegistry:
    standard = standard_task_parameters_schema()
    fastp_options = {
        name: {**standard['fastp_' + name], 'nextflow_parameter': 'fastp_' + name, 'optional': True}
        for name in ('unqualified_percent_limit', 'n_base_limit', 'cut_front', 'cut_tail',
                     'cut_window_size', 'cut_mean_quality', 'trim_poly_g', 'correction', 'detect_adapter_for_pe')
    }
    window_condition = {'any_of': [{'parameter': 'cut_front', 'equals': True}, {'parameter': 'cut_tail', 'equals': True}]}
    for name in ('cut_window_size', 'cut_mean_quality'):
        fastp_options[name]['active_when'] = window_condition
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
            method_id="fastqc",
            method_version="0.12.1",
            tool_requirements=("FastQC>=0.12",),
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
                **fastp_options,
                "qualified_quality_phred": parameter_schema("integer", minimum=1, maximum=93, default=20, title="合格碱基质量", group="scientific", unit="Q", nextflow_parameter="fastp_qualified_quality_phred"),
                "length_required": parameter_schema("integer", minimum=1, default=50, title="最低保留长度", group="scientific", unit="bp", nextflow_parameter="fastp_length_required"),
            },
            default_cpus=4,
            default_memory_gb=8,
            method_id="fastp",
            method_version="0.23.4",
            tool_requirements=("fastp>=0.23",),
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
                "filter_mode": parameter_schema("string", enum=("strict_both_unmapped", "concordant_unmapped"), default="strict_both_unmapped", title="宿主过滤模式", group="scientific", nextflow_parameter="host_filter_mode"),
                **{name: {**standard['host_' + name], 'nextflow_parameter': 'host_' + name, 'optional': True}
                   for name in ('min_retained_pairs', 'max_removed_pct', 'bowtie2_preset')},
            },
            default_cpus=4,
            default_memory_gb=8,
            database_requirements=("host.grch38_bowtie2",),
            method_id="bowtie2_host_depletion",
            method_version="2.5",
            tool_requirements=("Bowtie2>=2.5",),
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
                "read_length": parameter_schema("integer", enum=(50, 75, 100, 150, 200, 250, 300), default=150, title="Bracken 读长", group="scientific", unit="bp", nextflow_parameter="read_length"),
            },
            default_cpus=4,
            default_memory_gb=16,
            database_requirements=(
                "taxonomy_reads.kraken2",
                "taxonomy_reads.bracken",
            ),
            method_id="kraken2_bracken",
            method_version="2.1+2.9",
            tool_requirements=("Kraken2>=2.1", "Bracken>=2.9"),
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
            method_id="humann",
            method_version="3.9",
            tool_requirements=("HUMAnN>=3.9", "MetaPhlAn>=4"),
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
                **threads_schema(8),
                **memory_schema(32, nextflow_parameter="mag_memory_gb"),
                "assembler": parameter_schema("string", enum=("megahit", "metaspades"), default="megahit", title="组装器", group="scientific", nextflow_parameter="assembler"),
            },
            default_cpus=8,
            default_memory_gb=32,
            method_id="megahit_metaspades",
            method_version="1.0",
            tool_requirements=("MEGAHIT", "metaSPAdes"),
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
            method_id="metabat2_binning",
            method_version="2.15",
            tool_requirements=("MetaBAT2",),
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
                "completeness": parameter_schema("number", minimum=0, maximum=100, default=70, title="最低完整度", group="scientific", unit="%", nextflow_parameter="bin_completeness"),
                "contamination": parameter_schema("number", minimum=0, maximum=100, default=5, title="最高污染度", group="scientific", unit="%", nextflow_parameter="bin_contamination"),
            },
            default_cpus=8,
            default_memory_gb=32,
            method_id="checkm_refinement",
            method_version="1.0",
            tool_requirements=("CheckM2",),
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
            method_id="coverm_quantification",
            method_version="0.7",
            tool_requirements=("CoverM",),
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
            method_id="mag_reassembly",
            method_version="1.0",
            tool_requirements=("SPAdes",),
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
            method_id="mag_annotation",
            method_version="1.0",
            tool_requirements=("GTDB-Tk", "eggNOG-mapper"),
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
            method_id="biollm_report",
            method_version="1.0",
            tool_requirements=("BioLLM report",),
        ),
    )
    return {definition.type: definition for definition in definitions}


def standard_task_parameters_schema() -> dict[str, dict[str, Any]]:
    return {
        "threads": parameter_schema("integer", minimum=1, maximum=256, default=4, title="计算线程", group="resources", unit="threads"),
        "fastp_qualified_quality_phred": parameter_schema("integer", minimum=1, maximum=93, default=20, title="fastp 质量阈值", group="scientific", unit="Q"),
        "fastp_unqualified_percent_limit": parameter_schema("integer", minimum=0, maximum=100, default=40, title="低质量碱基上限", group="scientific", unit="%"),
        "fastp_n_base_limit": parameter_schema("integer", minimum=0, default=5, title="N 碱基上限", group="scientific"),
        "fastp_length_required": parameter_schema("integer", minimum=1, default=50, title="最低保留长度", group="scientific", unit="bp"),
        "fastp_cut_front": parameter_schema("boolean", default=True, title="前端滑窗剪切", group="scientific"),
        "fastp_cut_tail": parameter_schema("boolean", default=True, title="尾端滑窗剪切", group="scientific"),
        "fastp_cut_window_size": parameter_schema("integer", minimum=1, default=4, title="滑窗大小", group="scientific", unit="bp"),
        "fastp_cut_mean_quality": parameter_schema("integer", minimum=1, maximum=93, default=20, title="滑窗平均质量", group="scientific", unit="Q"),
        "fastp_trim_poly_g": parameter_schema("boolean", default=True, title="PolyG 剪切", group="scientific"),
        "fastp_correction": parameter_schema("boolean", default=False, title="双端纠错", group="scientific"),
        "fastp_detect_adapter_for_pe": parameter_schema("boolean", default=True, title="自动识别接头", group="scientific"),
        "host_filter_mode": parameter_schema("string", enum=("strict_both_unmapped", "concordant_unmapped"), default="strict_both_unmapped", title="宿主过滤模式", group="scientific"),
        "host_min_retained_pairs": parameter_schema("integer", minimum=0, default=0, title="最少保留 read pairs", group="scientific"),
        "host_max_removed_pct": parameter_schema("number", minimum=0, maximum=100, default=100, title="最大去除比例", group="scientific", unit="%"),
        "host_bowtie2_preset": parameter_schema("string", enum=("very-fast", "fast", "sensitive", "very-sensitive"), default="very-sensitive", title="Bowtie2 灵敏度", group="scientific"),
        "read_length": parameter_schema("integer", enum=(50,75,100,150,200,250,300), default=150, title="Bracken 读长", group="scientific", unit="bp"),
        "enable_mags": parameter_schema("boolean", default=False, title="启用 MAG 分析", group="scientific"),
        "enable_reassembly": parameter_schema("boolean", default=True, title="候选 Bin 重组装", group="scientific", active_when={"parameter":"enable_mags", "equals": True}),
        "mag_threads": parameter_schema("integer", minimum=1, maximum=256, default=8, title="MAG 线程", group="resources", unit="threads", active_when={"parameter":"enable_mags", "equals": True}),
        "mag_memory_gb": parameter_schema("integer", minimum=4, default=32, title="MAG 内存", group="resources", unit="GB", active_when={"parameter":"enable_mags", "equals": True}),
        "assembler": parameter_schema("string", enum=("megahit", "metaspades"), default="megahit", title="组装器", group="scientific", active_when={"parameter":"enable_mags", "equals": True}),
        "bin_completeness": parameter_schema("number", minimum=0, maximum=100, default=70, title="最低完整度", group="scientific", unit="%", active_when={"parameter":"enable_mags", "equals": True}),
        "bin_contamination": parameter_schema("number", minimum=0, maximum=100, default=5, title="最高污染度", group="scientific", unit="%", active_when={"parameter":"enable_mags", "equals": True}),
        "host_index": parameter_schema("string", nullable=True, default=None, title="宿主参考索引", group="databases"),
        "kraken_db": parameter_schema("string", nullable=True, default=None, title="Kraken2 数据库", group="databases"),
        "humann_nucleotide_db": parameter_schema("string", nullable=True, default=None, title="HUMAnN 核酸库", group="databases"),
        "humann_protein_db": parameter_schema("string", nullable=True, default=None, title="HUMAnN 蛋白库", group="databases"),
        "metaphlan_db": parameter_schema("string", nullable=True, default=None, title="MetaPhlAn 数据库", group="databases"),
        "method_versions": parameter_schema("object", default={}, title="方法版本快照", group="provenance"),
        "taxonomy_plot_top_n": parameter_schema("integer", default=20, minimum=1, maximum=100, title="物种图 Top-N", group="figures", description="展示均值居前的物种；不改变完整物种矩阵上的多样性和距离计算。"),
        "taxonomy_plot_low_abundance_threshold": parameter_schema("number", default=0.01, minimum=0, maximum=1, title="物种组成图展示阈值", group="figures", unit="fraction", description="组成图按队列平均相对丰度过滤展示条目，0 表示不设阈值；省略条目归入 Other。"),
        "taxonomy_alpha_style": parameter_schema("string", default="auto", enum=("auto","points","box","violin"), title="α 多样性图型", group="figures", description="auto 自动选择；points 散点；box 箱线叠加样本点；violin 小提琴叠加样本点。分布图要求分组信息且每组至少 10 个样本，否则展示散点。这是显示策略，不是统计效能保证。"),
        "functional_plot_top_n": parameter_schema("integer", default=20, minimum=1, maximum=100, title="功能图 Top-N", group="figures", description="选择已命名功能进行展示；UNMAPPED 等诊断条目不进入功能组成分母。"),
        "functional_plot_scale": parameter_schema("string", default="relative", enum=("relative","reported"), title="功能热图数值口径", group="figures", description="relative：按样本全部已命名功能之和归一化；reported：log1p 原始报告值，不代表经标准化的跨样本绝对丰度。覆盖度始终保留 0–1 原值。"),
        "figure_extended_plots": parameter_schema("boolean", default=True, title="生成扩展科研图", group="figures", description="增加秩丰度、优势特征点图、样本距离图；数据满足条件时增加聚类图与检出频率图。"),
        "figure_ai_advice": parameter_schema("boolean", default=False, title="本地 AI 选图建议", group="figures", description="向本地模型提供 Skill 方法和匿名计数摘要，只选择已注册图型；10 秒请求超时后使用规则方案。不自动开展显著性检验。"),
    }

def standard_method_versions() -> dict[str, str]:
    return {
        definition.method_id or definition.type: definition.method_version or definition.version
        for definition in default_node_registry().values()
        if definition.type != "fastq_input"
    }


def serialize_node_registry(registry: NodeRegistry) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for definition in registry.values():
        nodes.append(
            {
                "type": definition.type,
                "version": definition.version,
                "label": definition.label,
                "category": definition.category,
                "method_id": definition.method_id or definition.type,
                "method_version": definition.method_version or definition.version,
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
                "tool_requirements": list(definition.tool_requirements),
            }
        )
    return {"registry_version": REGISTRY_VERSION, "nodes": nodes}
