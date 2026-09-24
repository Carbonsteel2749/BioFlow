# Methods · Clinical / Exposure / Microbiome / Statistics 槽位

对照写作经验 PDF §4.4.2–4.4.5 与钙–ASD 范文 Materials and Methods（量表、发钙、菌群测序、统计）。

**结论：四段都需要槽位 + LLM 提示词**（研究特异、不可编造试剂盒/阈值/量表条目数）。

实现：`article_writing/prompts/methods_remaining.py`  
写入：`sections/methods.py`（Participants 之后四节）  
润色：`METHODS_REMAINING_LLM_PROMPT` 并入 `llm_instructions_extra`

---

## 4.4.2 Clinical assessment（临床量表）

| 槽位 | 字段 | 说明 |
|------|------|------|
| 总起 | `PaperBrief.clinical_assessment_lead_in` | 可选；无则用默认总起句 |
| 量表列表 | `AnalysisBundle.clinical_scales`（优先）或 `PaperBrief.clinical_scales` | `ClinicalScale` 列表 |
| 全称/缩写 | `name` / `abbreviation` | 禁止只写缩写 |
| 条目/维度 | `n_items` / `domains` / `description` | |
| 施测者与方式 | `administered_by` / `administration_method` | 家长 / 临床评估者等 |
| 分数含义 | `score_interpretation` | 如 ATEC：分低损害轻 |
| 引用 | `citation` | 如 `[21]` |

---

## 4.4.3 Exposure measurement（核心变量测量）

`AnalysisBundle.exposure_measurement` → `ExposureMeasurement`

| 槽位 | 字段 |
|------|------|
| 变量名 | `variable_name` |
| 采集部位 | `collection_site` |
| 处理流程 | `processing_workflow` |
| 检测技术 | `detection_method` |
| 质控 | `quality_control` |
| 单位 | `units` |
| 分组标准 | `grouping_criteria` |
| 补充 | `additional_notes` |

---

## 4.4.4 Microbiome sequencing（菌群测序）

`AnalysisBundle.microbiome_sequencing` → `MicrobiomeSequencing`

| 槽位 | 字段 |
|------|------|
| 粪便采集 | `sample_collection` |
| 保存 | `sample_storage` |
| DNA 提取 | `dna_extraction` |
| 建库（可选） | `library_prep` |
| 平台 / 策略 | `sequencing_platform` / `sequencing_strategy` |
| 数据质控 | `qc_pipeline` |
| 物种注释 | `taxonomy_annotation` |
| 通路注释 | `pathway_annotation` |
| 过滤后摘要 | `post_filter_summary` |

---

## 4.4.5 Statistical analysis（统计分析）

`AnalysisBundle.statistical_analysis` → `StatisticalAnalysisPlan`

| 槽位 | 字段 |
|------|------|
| 总述 | `analysis_overview` |
| 组间比较 | `group_comparison_method` |
| 连续相关 | `continuous_association_method` |
| 多变量/整合模型 | `multivariate_models` |
| 协变量 | `covariates` |
| 多重校正 | `multiple_testing_correction` |
| 显著性阈值 | `significance_threshold` |
| 软件 | `software` |

若结构化计划为空，Methods 回退到 `analysis.methods.differential_analysis` 字典（兼容旧 fixture）。

## LLM 硬约束

只润色、中英双语、**不得编造**量表条目数、试剂盒名、平台、q 值阈值、协变量列表；缺槽保留占位并告警。
