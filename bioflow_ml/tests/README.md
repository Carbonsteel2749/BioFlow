# 测试约定

每位成员为自己的功能添加 `test_*.py`。至少覆盖：正常输入、异常输入、输出形状/字段和随机种子可复现性。

统一测试数据在项目根目录的 `fixtures.py`：

-`make_classification_fixture()`：供监督分类和特征选择使用；

-`make_clustering_fixture()`：供 PCA 和聚类使用。

建议测试文件按职责命名，避免多人编辑同一文件：

```text

test_preprocessing.py

test_unsupervised.py

test_supervised.py

test_feature_selection.py

```
