"""Closed skill catalog shared by the planner, manual actions and worker."""

from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True)
class Spec:
    id: str
    name: str
    description: str
    requires: str
    version: str = "1.0.0"
    outputs: tuple[str, ...] = ()


SPECS = [
    Spec(
        "join_tables",
        "按编号合并表格",
        "按明确的一对一标识连接两份样本表或普通表；输出匹配审计和合并表，不猜测多对多连接。",
        "research",
        outputs=("joined", "join_audit"),
    ),
    Spec(
        "baseline_table",
        "分组基线表",
        "两组数值均值/标准差、中位数/四分位数、类别n/N与缺失分母；Mann–Whitney或Welch、Fisher/卡方及可选BH。",
        "research",
        outputs=("baseline",),
    ),
    Spec(
        "partial_correlation",
        "偏相关与批量相关",
        "单指标对多个量表或单题；可控制数值/分类协变量，秩先变换的偏Spearman、Pearson或明确选择旧版残差Spearman；全部检验后BH。",
        "research",
        outputs=("associations", "analysis_audit"),
    ),
    Spec(
        "adjusted_regression",
        "协变量校正回归",
        "表格、物种或通路对多结局的OLS，HC3或经典标准误、可选CLR/log1p、标准化效应和95%CI；支持候选结果表输入，逐检验记录样本与家族BH。",
        "research",
        outputs=("associations", "analysis_audit"),
    ),
    Spec(
        "association_heatmap",
        "关联效应热图",
        "从已有完整关联结果绘制效应热图，颜色对应真实效应、星号对应q或明确选择的p，不重新计算或补造显著性。",
        "research",
        outputs=("plot_data",),
    ),
    Spec(
        "effect_plot",
        "效应与置信区间图",
        "从已有结果绘制森林/效应点图，保留效应、95%CI和q；需明确效应量含义，不能把LDA、rho、beta混用。",
        "research",
        outputs=("plot_data",),
    ),
    Spec(
        "association_network",
        "三层关联桑基图",
        "连接两份相邻层关联表，显式按q/p筛选，边宽使用绝对效应，保留符号和来源，展示关联而非因果或代谢通量。",
        "research",
        outputs=("network_edges", "network_nodes"),
    ),
    Spec(
        "custom_analysis",
        "按研究目标编写分析",
        "现有工具无法表达时，按确认的方法与验收要求生成 Python 分析，隔离执行、检查输出、失败后按原目标修正。",
        "custom",
        version="1.2.0",
    ),
    Spec(
        "prepare_cohort",
        "准备分析队列",
        "按明确规则依次筛选、计算新列、分位数或阈值分组、重编码；输出纳入名单、排除原因和实际阈值。",
        "any",
    ),
    Spec(
        "group_feature_summary",
        "各组优势特征",
        "在指定队列内按组独立计算均值、中位数、检出率和 Top N 排名，分别作图；描述性优势不等于显著富集。",
        "groups",
    ),
    Spec(
        "table_overview",
        "表格概览",
        "查看一张表的列、缺失、数值分布与类别，不预设研究分组。",
        "table",
    ),
    Spec(
        "field_distribution",
        "字段分布",
        "对一张表中的一个或多个字段做分布：数值给出直方图和描述统计，类别给出频数，有序等级按已确认的顺序排列。",
        "table",
    ),
    Spec(
        "table_correlation",
        "两字段相关分析",
        "对一张表中指定的两个字段进行 Spearman 相关，输出散点图与有效观测。字段可以是数值，或已确认等级顺序的有序等级。",
        "table",
        version="1.1.0",
    ),
    Spec(
        "group_numeric_compare",
        "分组数值比较",
        "对样本表或普通表中的一个数值字段，按分类列分成两组或多组，做 Mann–Whitney 或 Kruskal–Wallis，并给出各组中位数与箱线图。不校正协变量。",
        "table",
        version="1.0.0",
    ),
    Spec(
        "sankey",
        "桑基图",
        "从已有关系表的起点、终点和非负权重绘制桑基图，保留完整边表。",
        "table",
    ),
    Spec(
        "taxa_association",
        "连续指标与物种关联",
        "按样本对应连续指标与物种相对丰度，Spearman 相关及 BH 校正。必须指定 variable。",
        "taxa_numeric",
    ),
    Spec(
        "diversity_association",
        "连续指标与多样性关联",
        "连续指标与四项 Alpha 多样性关联，Spearman 相关及 BH 校正。必须指定 variable。",
        "taxa_numeric",
    ),
    Spec(
        "functional_association",
        "连续指标与通路关联",
        "连续指标与群落总通路丰度关联，不使用物种分层行。必须指定 variable。",
        "functional_numeric",
    ),
    Spec(
        "variable_distribution",
        "连续指标分布",
        "指定数值字段的分布、缺失数与描述统计。必须指定 variable。",
        "metadata_numeric",
    ),
    Spec("data_check", "数据体检", "检查数据规模、单位、样本与可用能力。", "any"),
    Spec(
        "metadata_overview",
        "样本特征概览",
        "样本字段缺失率、数值分布与分类构成，不推断分组。",
        "metadata",
    ),
    Spec(
        "composition",
        "菌群组成",
        "门/科/属/种组成、检出率、Top N 堆叠图与热图；可限定样本。",
        "taxonomy",
        version="1.1.0",
    ),
    Spec(
        "sample_profile",
        "指定样本组成",
        "查看一个或多个具名样本在指定分类层级的组成剖面与优势特征。",
        "taxonomy",
    ),
    Spec(
        "sample_comparison",
        "指定样本组成对比",
        "对两个及以上具名样本做描述性组成对比：丰度对照、共有/特有检出与并排图。不做统计检验。",
        "taxonomy",
    ),
    Spec(
        "core_taxa",
        "核心与共有特征",
        "按检出率阈值识别核心菌；有分组时给出各组核心及共有/特有集合。",
        "taxonomy",
    ),
    Spec(
        "alpha_diversity",
        "Alpha 多样性",
        "Shannon、Simpson、检出特征数与均匀度。",
        "taxonomy",
        version="1.1.0",
    ),
    Spec(
        "beta_diversity",
        "Beta 多样性",
        "Bray–Curtis 距离与 PCoA，保留负特征值警告。",
        "taxonomy",
        version="1.0.1",
    ),
    Spec(
        "alpha_comparison",
        "多样性组间检验",
        "两组 Mann–Whitney 或三组及以上 Kruskal–Wallis；BH 校正。",
        "groups",
        version="1.1.0",
    ),
    Spec(
        "community_comparison",
        "群落组间检验",
        "两组或多组 PERMANOVA 与 PERMDISP，固定随机种子。",
        "groups",
        version="1.1.0",
    ),
    Spec(
        "differential",
        "探索性物种差异",
        "检出率过滤后两组秩和或三组及以上 Kruskal–Wallis，BH 校正，并给出丰度对比。",
        "groups",
        version="1.2.0",
        outputs=("differential",),
    ),
    Spec(
        "functional_overview",
        "通路概览",
        "仅使用群落总丰度行，排除特殊条目。",
        "functional_total",
    ),
    Spec(
        "functional_differential",
        "探索性通路差异",
        "群落总通路丰度的两独立组探索性比较。",
        "functional_groups",
        outputs=("differential",),
    ),
    Spec(
        "pathway_contribution",
        "通路物种贡献",
        "展示指定通路分层丰度，绝不恢复群落总丰度。",
        "functional_stratified",
    ),
    Spec(
        "report",
        "研究报告",
        "把计算结果、图表、方法和局限汇总为可编辑报告。",
        "results",
    ),
]
# All reusable tables are explicit capabilities, not guessed file names.
SOURCE_OUTPUTS = {
    "taxa_association": ("associations", "matched_observations"),
    "functional_association": ("associations", "matched_observations"),
    "diversity_association": ("associations", "matched_observations"),
    "table_correlation": ("correlation", "observations"),
    "prepare_cohort": ("cohort_membership", "prepared_metadata", "preparation_audit"),
    "group_feature_summary": ("group_feature_ranks",),
    "pathway_contribution": ("pathway_contributions",),
    "alpha_diversity": ("alpha_diversity",),
    "beta_diversity": ("pcoa_coordinates",),
}
SPECS = [
    replace(spec, outputs=SOURCE_OUTPUTS[spec.id], version="1.2.0")
    if spec.id in SOURCE_OUTPUTS
    else spec
    for spec in SPECS
]
CATALOG = {s.id: s for s in SPECS}


def catalog():
    from ..contracts import SkillParams

    return [{**asdict(s), "parameters_schema": SkillParams.model_json_schema()} for s in SPECS]
