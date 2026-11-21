# BioFlow ML 算法能力目录

> 动态注册表代码：`catalog.py`。本页是便于汇报和组员查看的目录快照；运行时状态以 `print_algorithm_catalog()` 的输出为准。

状态说明：`ready` 表示当前服务器具备 Python 依赖；`requires_setup` 表示算法代码已登记，但尚缺依赖或环境配置。

| 算法 | 适用数据 | 输入要求 | 主要输出 | 状态 | 功能 |
| --- | --- | --- | --- | --- | --- |
| `pca` | 基因表达、菌群、代谢组 | 样本 × 特征；无需标签 | PC 坐标、方差解释率、特征载荷 | ready | 主成分降维与样本结构探索 |
| `kmeans` | 基因表达、菌群、代谢组 | 样本 × 特征；指定簇数 | 聚类标签、inertia、轮廓系数 | ready | K-means 样本聚类 |
| `hierarchical` | 基因表达、菌群、代谢组 | 样本 × 特征；指定簇数 | 聚类标签、轮廓系数 | ready | 层次聚类 |
| `tsne` | 基因表达、菌群、代谢组 | 样本 × 特征；perplexity 小于样本数 | 2D/3D 嵌入坐标 | ready | 非线性可视化 |
| `umap` | 基因表达、菌群、代谢组 | 样本 × 特征 | 2D/3D 嵌入坐标 | requires_setup | 非线性可视化；服务器缺 `umap-learn` |
| `logistic_regression` | 基因表达、菌群、代谢组、临床表格 | 样本 × 特征；必须有分类标签 | CV 指标、预测、概率、系数 | ready | 逻辑回归分类 |
| `svm` | 基因表达、菌群、代谢组、临床表格 | 样本 × 特征；必须有分类标签 | CV 指标、预测、分类器 | ready | 支持向量机分类 |
| `random_forest` | 基因表达、菌群、代谢组、临床表格 | 样本 × 特征；必须有分类标签 | CV 指标、预测、特征重要性 | ready | 随机森林分类 |
| `lasso` | 基因表达、菌群、代谢组、临床表格 | 样本 × 特征；必须有分类标签 | 候选特征、权重、CV 指标 | ready | L1 正则特征选择 |
| `rfe` | 基因表达、菌群、代谢组、临床表格 | 样本 × 特征；必须有分类标签 | 特征排序、候选特征、分类结果 | ready | 递归特征消除 |

## 查看动态目录

```bash
cd /home/xh/BioFLow
python3 -c "from bioflow_ml import print_algorithm_catalog; print_algorithm_catalog()"
```

只查看基因表达适用算法：

```bash
python3 -c "from bioflow_ml import print_algorithm_catalog; print_algorithm_catalog(domain='gene_expression')"
```

给前端/API 返回 JSON：

```python
from bioflow_ml import get_algorithm_catalog
catalog = get_algorithm_catalog(domain="microbiome")
```
