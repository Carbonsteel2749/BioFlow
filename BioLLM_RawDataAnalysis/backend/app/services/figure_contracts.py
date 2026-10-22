"""Public redraw contracts: registered numeric methods and bounded parameters."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SOURCE_TYPES = {
    'taxonomy': 'taxonomy.species_abundance',
    'gene_families': 'functional.gene_families',
    'ko': 'functional.ko_abundance', 'ec': 'functional.ec_abundance',
    'pathway_abundance': 'functional.pathway_abundance',
    'pathway_coverage': 'functional.pathway_coverage',
}


def chart(identifier, label, samples=1, features=1, meaning=''):
    return dict(id=identifier, label=label, minimum_samples=samples, minimum_features=features, meaning=meaning)


TAXONOMY_CHARTS = [
    chart('stacked', '物种组成堆叠图', meaning='Bracken 报告比例；Top-N 之外保留 Other'),
    chart('heatmap', '物种组成热图'),
    chart('alpha_diversity', 'α 多样性四指标', meaning='单样本显示四指标摘要，多样本显示点图；有足够组内重复时可展示分布。使用全部物种计算，不受 Top-N 或显示阈值影响'),
    chart('pcoa', 'Bray–Curtis PCoA', 2, meaning='描述样本组成距离；不自动添加显著性或置信椭圆'),
    chart('rank_abundance', '秩丰度曲线'),
    chart('dominant_species_dot', '优势物种点图'),
    chart('prevalence_abundance', '检出频率与平均丰度', 3, 2),
    chart('distance_heatmap', 'Bray–Curtis 距离热图', 2),
    chart('clustered_heatmap', '物种描述性聚类热图', 3, 2),
]
FUNCTIONAL_CHARTS = [
    chart('heatmap', '功能热图'), chart('composition', '功能组成图'),
    chart('feature_dot', '优势功能点图'),
    chart('functional_clustered_heatmap', '功能描述性聚类热图', 3, 2),
]
CHARTS = {'taxonomy': TAXONOMY_CHARTS, **{key: FUNCTIONAL_CHARTS for key in SOURCE_TYPES if key != 'taxonomy'}}


class FigureParameters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    top_n: int = Field(default=20, ge=1, le=100, strict=True, description='展示排名靠前的条目数量；不改变多样性计算矩阵')
    low_abundance_threshold: float = Field(default=.01, ge=0, le=1, allow_inf_nan=False, description='物种组成图筛选的平均比例阈值；0 表示不额外过滤')
    alpha_style: Literal['auto', 'points', 'box', 'violin'] = Field(default='auto', description='α 多样性展示方式；单样本固定显示数值摘要，分组重复不足时保留真实样本点并说明原因')
    display_scale: Literal['relative', 'reported'] = Field(default='relative', description='功能热图：已命名功能内比例，或 log1p 原始报告值；覆盖度固定为 0–1')
    palette: Literal['default', 'cividis', 'viridis', 'magma'] = Field(default='default', description='连续数值图的配色；分类图使用固定可区分颜色')
    font_size: int = Field(default=10, ge=8, le=18, strict=True, description='图中文字基准大小（pt）')
    width_inches: float | None = Field(default=None, ge=5, le=20, allow_inf_nan=False, description='导出宽度（英寸）；留空使用图型自适应尺寸')
    height_inches: float | None = Field(default=None, ge=4, le=20, allow_inf_nan=False, description='导出高度（英寸）；留空使用图型自适应尺寸')
    ai_advice: bool = Field(default=False, strict=True, description='可选 AI 选图建议；失败时保留规则方法，不阻止绘图')


class FigureRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    data_type: Literal['taxonomy', 'gene_families', 'ko', 'ec', 'pathway_abundance', 'pathway_coverage']
    chart: str = Field(max_length=60)
    source_artifact_ids: list[str] = Field(min_length=1, max_length=100)
    sample_ids: list[str] = Field(min_length=1, max_length=100)
    groups: dict[str, str] = Field(default_factory=dict)
    parameters: FigureParameters = Field(default_factory=FigureParameters)
