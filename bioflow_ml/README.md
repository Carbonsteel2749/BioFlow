# BioFLow 第二板块：机器学习算法库（协作骨架）

本目录是第二板块的协作开发骨架。第一期目标是构建可被 BioFLow 调用的生信机器学习算法库，支持已处理表达矩阵或由本库预处理后的矩阵。

## 第一期开范围

- 输入文件：CSV、XLSX、TSV、TXT。
- 输入数据：表达矩阵、微生物丰度或临床表格等样本 × 特征数据。
- 数据预处理：文件读取、counts/normalized 状态识别、必要时 CPM + log1p、样本标签对齐。
- 算法：PCA、k-means、层次聚类、Logistic Regression、SVM、Random Forest、LASSO、RFE。
- 评估：分层交叉验证、Accuracy、F1、Precision、Recall、二分类 AUROC。

不包含：原始测序 QC、差异表达分析、GO/KEGG、GSEA、WGCNA、AI skills 和 BioFLow 主工程接入。

## 四人分工

| 人员 | 负责目录/文件 | 任务 |

| --- | --- | --- |

| 负责成员 | `core.py`、`evaluation.py`、`fixtures.py`、本 README | 维护统一接口、算法注册、共用模拟数据、评估规范、整合与验收 |

| 成员 A | `preprocessing/` | 文件读取、数据状态识别、归一化、矩阵转换、metadata 标签对齐 |

| 成员 B | `algorithms/unsupervised/` | PCA、k-means、层次聚类 |

| 成员 C | `algorithms/supervised/`、`algorithms/feature_selection/` | 分类模型与特征选择 |

## 共同接口（必须遵守）

所有算法都接收 `MLInput`，返回 `AlgorithmResult`：

```text

MLInput

- X: pandas.DataFrame，行=样本，列=特征

- y: pandas.Series | None，与 X 行索引严格对齐

- task: classification / clustering / feature_selection

- params: 当前算法参数字典


AlgorithmResult

- algorithm: 算法注册名称

- metrics: 评估指标字典

- labels / predictions: 聚类标签或分类预测

- transformed_data: PCA / 特征选择后的矩阵（可选）

- feature_scores: 特征重要性或选择排序（可选）

- model: 已拟合模型（可选）

- metadata / warnings: 运行记录和警告

```

新增算法必须：

1. 继承 `BaseAlgorithm`；
2. 声明唯一的 `AlgorithmSpec`；
3. 用 `@register_algorithm` 注册；
4. 实现 `run(data: MLInput) -> AlgorithmResult`；
5. 在 `tests/` 添加至少一个测试。

## 算法目录（注册表）

导入 `bioflow_ml` 后，内置算法会自动注册。前端或编排器可以使用
`get_algorithm_catalog()` 取得 JSON 友好的算法目录，其中包含算法名称、版本、
支持任务和功能说明；命令行演示可使用 `print_algorithm_catalog()`。

```python
from bioflow_ml import get_algorithm_catalog, print_algorithm_catalog, registry

print(get_algorithm_catalog(task="classification"))
print_algorithm_catalog()

algorithm = registry.create("random_forest", n_estimators=300, random_state=42)
```

算法实现仍必须通过 `@register_algorithm` 注册；不要由前端维护另一份算法名单。

## 开发顺序

1. 组长先确认 `core.py` 和 `evaluation.py` 的接口不再随意变更。
2. 三位成员在自己目录中独立实现，不直接改公共层。
3. 每人提交代码、测试、使用示例和算法说明。
4. 组长统一注册、运行测试，并用完整真实数据做流程演示。

## 安装与测试

```bash

pip install -r requirements.txt

python -m pytest -q

```

## Microbiome and metabolomics analysis

`microbiome_analysis/` accepts abundance matrices in the same orientation as `MLInput`: rows are samples and columns are taxa, pathways, or metabolites. It provides:

- alpha diversity (observed features, Shannon, Simpson, Chao1);
- Bray-Curtis, Jaccard and Aitchison beta distances;
- PCoA, NMDS and permutation-based PERMANOVA;
- CLR transformation, two-group Mann-Whitney/Welch tests and Benjamini-Hochberg FDR;
- Spearman association networks and Mantel tests for matched sample sets.

`metabolomics/preprocessing.py` provides an auditable baseline for half-minimum imputation, total-sum normalization and log1p transformation. Batch correction is intentionally not applied automatically because it requires a confirmed batch annotation and design.

These functions analyse already generated abundance/feature tables. Raw 16S and shotgun FASTQ processing remains the responsibility of QIIME2/DADA2 or the tool wrappers in `bioinformatics/`.
