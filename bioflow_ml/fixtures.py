"""开发与测试用的固定随机模拟数据；不代表真实生物学结论。"""

from __future__ import annotations

import pandas as pd
from sklearn.datasets import make_blobs, make_classification


def make_classification_fixture(
    n_samples: int = 60,
    n_features: int = 30,
    random_state: int = 42,
) -> tuple[pd.DataFrame, pd.Series]:
    """返回有标签的样本 × 特征矩阵，供分类与特征选择测试使用。"""

    values, labels = make_classification(
        n_samples=n_samples,
        n_features=n_features,
        n_informative=min(8, n_features - 2),
        n_redundant=min(4, max(0, n_features - 10)),
        n_classes=2,
        class_sep=1.5,
        random_state=random_state,
    )
    sample_ids = [f"sample_{number:03d}" for number in range(n_samples)]
    feature_ids = [f"feature_{number:03d}" for number in range(n_features)]
    return (
        pd.DataFrame(values, index=sample_ids, columns=feature_ids),
        pd.Series(labels, index=sample_ids, name="label"),
    )


def make_clustering_fixture(
    n_samples: int = 60,
    n_features: int = 12,
    n_clusters: int = 3,
    random_state: int = 42,
) -> pd.DataFrame:
    """返回具有明显簇结构的无标签矩阵，供 PCA 和聚类测试使用。"""

    values, _ = make_blobs(
        n_samples=n_samples,
        n_features=n_features,
        centers=n_clusters,
        cluster_std=1.1,
        random_state=random_state,
    )
    return pd.DataFrame(
        values,
        index=[f"sample_{number:03d}" for number in range(n_samples)],
        columns=[f"feature_{number:03d}" for number in range(n_features)],
    )
