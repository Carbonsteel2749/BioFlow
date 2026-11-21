"""面向前端、编排器和汇报展示的 BioFlow ML 算法能力目录。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.util import find_spec

from .core import AlgorithmRegistry, AlgorithmSpec, registry


@dataclass(frozen=True)
class AlgorithmCapabilityProfile:
    """算法的领域、输入输出和运行依赖说明。"""

    domains: tuple[str, ...]
    input_requirements: tuple[str, ...]
    output_description: str
    notes: str
    requirements: tuple[str, ...] = ()


_PROFILES: dict[str, AlgorithmCapabilityProfile] = {
    "pca": AlgorithmCapabilityProfile(
        domains=("gene_expression", "microbiome", "metabolomics"),
        input_requirements=("样本 × 特征数值矩阵", "不需要分组标签"),
        output_description="主成分坐标、方差解释率和特征载荷。",
        notes="适合探索样本分组、批次效应和离群样本。",
    ),
    "kmeans": AlgorithmCapabilityProfile(
        domains=("gene_expression", "microbiome", "metabolomics"),
        input_requirements=("样本 × 特征数值矩阵", "需指定聚类数 n_clusters"),
        output_description="样本聚类标签、簇内平方和和轮廓系数。",
        notes="聚类数应结合生物学问题和轮廓系数判断。",
    ),
    "hierarchical": AlgorithmCapabilityProfile(
        domains=("gene_expression", "microbiome", "metabolomics"),
        input_requirements=("样本 × 特征数值矩阵", "需指定聚类数 n_clusters"),
        output_description="样本聚类标签和轮廓系数。",
        notes="适合观察样本间层级结构；可选择 linkage 与距离度量。",
    ),
    "tsne": AlgorithmCapabilityProfile(
        domains=("gene_expression", "microbiome", "metabolomics"),
        input_requirements=("样本 × 特征数值矩阵", "perplexity 必须小于样本数"),
        output_description="二维或三维非线性嵌入坐标。",
        notes="主要用于可视化；不同随机种子或参数可能改变图形布局。",
    ),
    "umap": AlgorithmCapabilityProfile(
        domains=("gene_expression", "microbiome", "metabolomics"),
        input_requirements=("样本 × 特征数值矩阵", "推荐先做标准化或特征筛选"),
        output_description="二维或三维非线性嵌入坐标。",
        notes="主要用于可视化；应记录邻居数、距离度量和随机种子。",
        requirements=("umap-learn",),
    ),
    "logistic_regression": AlgorithmCapabilityProfile(
        domains=("gene_expression", "microbiome", "metabolomics", "clinical_tabular"),
        input_requirements=("样本 × 特征数值矩阵", "每个样本必须有分类标签"),
        output_description="交叉验证指标、预测标签、预测概率和特征系数。",
        notes="高维小样本结果仅用于探索；需独立队列验证泛化性。",
    ),
    "svm": AlgorithmCapabilityProfile(
        domains=("gene_expression", "microbiome", "metabolomics", "clinical_tabular"),
        input_requirements=("样本 × 特征数值矩阵", "每个样本必须有分类标签"),
        output_description="交叉验证指标、预测标签和拟合分类器。",
        notes="应通过交叉验证调节核函数、C 等超参数。",
    ),
    "random_forest": AlgorithmCapabilityProfile(
        domains=("gene_expression", "microbiome", "metabolomics", "clinical_tabular"),
        input_requirements=("样本 × 特征数值矩阵", "每个样本必须有分类标签"),
        output_description="交叉验证指标、预测标签和特征重要性。",
        notes="特征重要性反映当前数据的预测贡献，不直接等同于生物学机制。",
    ),
    "lasso": AlgorithmCapabilityProfile(
        domains=("gene_expression", "microbiome", "metabolomics", "clinical_tabular"),
        input_requirements=("样本 × 特征数值矩阵", "每个样本必须有分类标签"),
        output_description="选中特征、特征权重、交叉验证指标和拟合模型。",
        notes="用于候选基因/菌群特征筛选，不可单独作为机制或因果结论。",
    ),
    "rfe": AlgorithmCapabilityProfile(
        domains=("gene_expression", "microbiome", "metabolomics", "clinical_tabular"),
        input_requirements=("样本 × 特征数值矩阵", "每个样本必须有分类标签"),
        output_description="递归筛选后的特征排序、选中特征和分类结果。",
        notes="高维小样本时应严格使用交叉验证，避免特征选择泄漏。",
    ),
}

# 分发包名与 Python 导入名不一致时在此声明。
_IMPORT_NAMES = {"umap-learn": "umap"}


@dataclass(frozen=True)
class AlgorithmCatalogEntry:
    """算法目录中的稳定展示条目。"""

    name: str
    version: str
    tasks: tuple[str, ...]
    description: str
    domains: tuple[str, ...]
    input_requirements: tuple[str, ...]
    output_description: str
    availability: str
    requirements: tuple[str, ...]
    notes: str

    @classmethod
    def from_spec(cls, spec: AlgorithmSpec) -> "AlgorithmCatalogEntry":
        profile = _PROFILES.get(
            spec.name,
            AlgorithmCapabilityProfile(
                domains=("general",),
                input_requirements=("请查看算法实现说明",),
                output_description="请查看 AlgorithmResult。",
                notes="该算法尚未补充领域说明。",
            ),
        )
        unavailable = [
            requirement
            for requirement in profile.requirements
            if find_spec(_IMPORT_NAMES.get(requirement, requirement)) is None
        ]
        return cls(
            name=spec.name,
            version=spec.version,
            tasks=tuple(spec.tasks),
            description=spec.description,
            domains=profile.domains,
            input_requirements=profile.input_requirements,
            output_description=profile.output_description,
            availability="ready" if not unavailable else "requires_setup",
            requirements=tuple(unavailable),
            notes=profile.notes,
        )

    def to_dict(self) -> dict[str, object]:
        """返回可直接序列化为 JSON 的前端/API 数据。"""

        payload = asdict(self)
        for key in ("tasks", "domains", "input_requirements", "requirements"):
            payload[key] = list(payload[key])
        return payload


def list_algorithms(
    *,
    task: str | None = None,
    domain: str | None = None,
    algorithm_registry: AlgorithmRegistry = registry,
) -> list[AlgorithmCatalogEntry]:
    """列出算法；可按任务类型或适用数据领域过滤。"""

    entries = [AlgorithmCatalogEntry.from_spec(spec) for spec in algorithm_registry.list_specs()]
    if task is not None:
        entries = [entry for entry in entries if task in entry.tasks]
    if domain is not None:
        entries = [entry for entry in entries if domain in entry.domains]
    return sorted(entries, key=lambda entry: entry.name)


def get_algorithm_catalog(
    *,
    task: str | None = None,
    domain: str | None = None,
    algorithm_registry: AlgorithmRegistry = registry,
) -> list[dict[str, object]]:
    """返回 JSON 友好的算法能力目录，供 API 或简易前端直接使用。"""

    return [
        entry.to_dict()
        for entry in list_algorithms(task=task, domain=domain, algorithm_registry=algorithm_registry)
    ]


def format_algorithm_catalog(
    *,
    task: str | None = None,
    domain: str | None = None,
    algorithm_registry: AlgorithmRegistry = registry,
) -> str:
    """生成人类可读的详细算法能力目录。"""

    entries = list_algorithms(task=task, domain=domain, algorithm_registry=algorithm_registry)
    title = "BioFlow ML Algorithm Catalog"
    if task is not None:
        title += f" [task={task}]"
    if domain is not None:
        title += f" [domain={domain}]"
    lines = [title, "=" * len(title)]
    if not entries:
        lines.append("(empty)")
    for entry in entries:
        lines.extend([
            f"- {entry.name}@{entry.version} [{', '.join(entry.tasks)}] | {entry.availability}",
            f"  适用数据: {', '.join(entry.domains)}",
            f"  功能: {entry.description}",
            f"  输入: {'；'.join(entry.input_requirements)}",
            f"  输出: {entry.output_description}",
            f"  注意: {entry.notes}",
        ])
        if entry.requirements:
            lines.append(f"  缺失依赖: {', '.join(entry.requirements)}")
    return "\n".join(lines)


def print_algorithm_catalog(**kwargs: object) -> None:
    """打印算法能力目录，便于命令行演示。"""

    print(format_algorithm_catalog(**kwargs))
