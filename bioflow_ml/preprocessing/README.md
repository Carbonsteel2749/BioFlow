# 统一数据预处理

在此目录实现，建议至少包含：

- CSV/XLSX/TSV/TXT 文件读取；
- 特征（基因）× 样本矩阵转为样本 × 特征；

-`counts`、`normalized`、`unknown` 的状态报告；

- counts 的低检出过滤、CPM 和 log1p；
- normalized 数据跳过 CPM/log1p；
- metadata 的 `sample_id` 与 `group` 标签严格对齐；
- 遇到缺失值、样本名不匹配、截断文件时明确报错。

预处理只运行一次，再把结果交给全部算法复用。不要把这部分逻辑复制到各算法文件。
