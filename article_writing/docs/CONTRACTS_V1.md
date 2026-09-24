# Contracts V1（冻结）— 成员 A 接口规范

| 项 | 内容 |
|----|------|
| 版本 | **V1**（Phase 1） |
| 冻结日期 | 2026-07-26 |
| 真源码 | `article_writing/contracts.py` |
| 样例数据 | `fixtures/*.json` |
| 审批人 | **成员 A**（接口法官） |
| 变更流程 | 提需求 → A 批准 → 改 `contracts.py` → 更新本文 + [SCHEMA_CHANGELOG.md](SCHEMA_CHANGELOG.md) → 通知 B/C/D/E/F |

**规则：** 任何人不得静默增删改字段名或改变必填语义。阶段 2+ 新字段先标「提案」，合入后升 V1.x / V2。

**状态图例：** `冻结` = 本阶段勿改；`建议必填` = 业务上应有值（Pydantic 可能仍给默认空）；`可选`；`延期` = 阶段 2+ 再加强。

---

## 1. SectionId

| 值 | 含义 | 状态 |
|----|------|------|
| `abstract` | 摘要 | 冻结（框架对齐） |
| `introduction` | 引言（Background→Gap→Objective） | 冻结 |
| `methods` | 方法 | 冻结（原 `data_method`） |
| `results` | 结果 | 冻结（原 `result`） |
| `discussion` | 讨论（解释/机制/局限） | 冻结 |
| `conclusion` | **独立结论（不并入 Discussion）** | 冻结 |
| `back_matter` | 数据/代码/伦理/作者/参考文献/补充 | 冻结 |

默认顺序：`abstract → introduction → methods → results → discussion → conclusion → back_matter`。  
说明见 [PAPER_FRAMEWORK.md](PAPER_FRAMEWORK.md)。

~~`related_work` / `data_method` / `result`~~ 已退役：文献写入 Intro/Discussion；方法/结果改用 `methods` / `results`。

---

## 2. Claim（论断）

| 字段 | 类型 | 状态 | 含义 | 生产者 | 消费者 |
|------|------|------|------|--------|--------|
| `claim_id` | str | 冻结 / 建议必填 | 全局唯一论断 id | C/D/E 各章 | E（Conclusion）、F、阶段2 evidence |
| `statement` | str | 冻结 / 建议必填 | 可核查陈述句 | 各章 | 全文 |
| `evidence_ids` | list[str] | 冻结 | 证据 id 列表；阶段1允许 Intro 暂时为空 | 各章 | 阶段2 强校验 |
| `section` | str \| null | 冻结 / 可选 | 所属章节 id | 各章 | 调试 / 导出 |

**约定：** Related / Result 的 claim 应尽量非空 `evidence_ids`（文献用 `paper_id`，分析用 `AnalysisBundle.evidence_ids` 中的 id）。

---

## 3. Citation（引用）

| 字段 | 类型 | 状态 | 含义 | 生产者 | 消费者 |
|------|------|------|------|--------|--------|
| `cite_id` | str | 冻结 / 建议必填 | 引用 id（可与 `paper_id` 相同） | C（Related）为主 | F、可视化、阶段3 |
| `title` | str | 冻结 / 建议必填 | 题名 | C / B fixtures | 全文 |
| `authors` | str | 冻结 / 可选 | 作者字符串 | 同上 | 同上 |
| `year` | int \| null | 冻结 / 可选 | 年 | 同上 | 同上 |
| `doi` | str \| null | 冻结 / 可选 | DOI | 同上 | 外组对齐 |
| `pmid` | str \| null | 冻结 / 可选 | PMID | 同上 | 外组对齐 |
| `snippet` | str | 冻结 / 可选 | 支撑摘录 | C | 防幻觉核对 |
| `source` | str | 冻结 | 来源标记，默认 `"fixture"` | B/C | 联调区分 mock/live |

---

## 4. FigureRef（图表引用）

| 字段 | 类型 | 状态 | 含义 | 生产者 | 消费者 |
|------|------|------|------|--------|--------|
| `figure_id` | str | 冻结 / 建议必填 | 图/表 id | B（分析包）/ D | F、板块5 |
| `path` | str | 冻结 / 建议必填 | 文件路径（阶段1可为 placeholder） | B | 板块5 |
| `caption` | str | 冻结 / 可选 | 题注 | B/D | 板块5 |
| `kind` | str | 冻结 | `"figure"` 或 `"table"` | B/D | 板块5 |

---

## 5. LiteratureHit（文献命中）— 对接文献板块

| 字段 | 类型 | 状态 | 含义 | 生产者 | 消费者 |
|------|------|------|------|--------|--------|
| `paper_id` | str | 冻结 / 建议必填 | 稳定文献 id | B fixtures → 未来文献库 | C Related |
| `title` | str | 冻结 / 建议必填 | 题名 | 同上 | C |
| `abstract` | str | 冻结 / 可选 | 摘要 | 同上 | C |
| `authors` | str | 冻结 / 可选 | 作者 | 同上 | C |
| `year` | int \| null | 冻结 / 可选 | 年 | 同上 | C |
| `doi` | str \| null | 冻结 / 可选 | DOI | 同上 | 外组 |
| `pmid` | str \| null | 冻结 / 可选 | PMID | 同上 | 外组 |
| `score` | float | 冻结 / 可选 | 检索相关分 | B mock search | C 排序 |
| `tags` | list[str] | 冻结 / 可选 | 标签 | B | C 过滤 |

样例文件：`fixtures/literature_hits.json`  
端口：`LiteraturePort.search` / `get`

**给文献组的对齐话术：** 请至少提供 `paper_id, title, abstract, doi|pmid`；其余可空。

---

## 6. AnalysisBundle（分析结论包）— 对接分析板块

| 字段 | 类型 | 状态 | 含义 | 生产者 | 消费者 |
|------|------|------|------|--------|--------|
| `summary` | str | 冻结 / 建议必填 | 一段话总结 | B → 未来分析板块 | D/E |
| `methods` | dict | 冻结 / 建议必填 | 方法键值（QC/归一化/DEG 参数等） | 同上 | D Method |
| `key_findings` | list[str] | 冻结 / 建议必填 | 关键发现列表 | 同上 | D Result |
| `metrics` | dict | 冻结 / 可选 | 数值指标 | 同上 | D Result |
| `limitations` | list[str] | 冻结 / 可选 | 局限 | 同上 | E Conclusion |
| `figure_refs` | list[FigureRef] | 冻结 / 可选 | 图表 | 同上 | D/F/板块5 |
| `evidence_ids` | list[str] | 冻结 / 建议必填 | 本包证据 id | 同上 | D claims |
| `raw` | dict | 冻结 / 可选 | 原始扩展字段（勿在章节里写死依赖） | 同上 | 调试 |
| `clinical_scales` | list[ClinicalScale] | V1.1 可选 | 临床量表槽位列表 | 临床/分析 | Methods Clinical assessment |
| `exposure_measurement` | ExposureMeasurement \| null | V1.1 可选 | 核心暴露/关键变量测量 | 实验/分析 | Methods Exposure measurement |
| `microbiome_sequencing` | MicrobiomeSequencing \| null | V1.1 可选 | 菌群测序全流程 | 实验/分析 | Methods Microbiome sequencing |
| `statistical_analysis` | StatisticalAnalysisPlan \| null | V1.1 可选 | 统计分析计划 | 分析 | Methods Statistical analysis |

嵌套模型字段见 [METHODS_REMAINING_SLOTS.md](METHODS_REMAINING_SLOTS.md)。

样例：`fixtures/analysis_bundle.json`  
端口：`AnalysisPort.load`

**给分析组的对齐话术：** 请输出能填满上表的 JSON；`methods` / `key_findings` / `evidence_ids` 优先；临床/暴露/测序/统计有结构化数据时填 V1.1 槽位，勿让 LLM 编造。

---

## 7. PaperBrief（课题简介）

| 字段 | 类型 | 状态 | 含义 | 生产者 | 消费者 |
|------|------|------|------|--------|--------|
| `title` | str | 冻结 / 建议必填 | 题名 | B | C Intro、F |
| `research_question` | str | 冻结 / 建议必填 | 研究问题 | B | C Intro |
| `organism_or_system` | str | 冻结 / 可选 | 体系/物种 | B | C |
| `data_modality` | str | 冻结 / 可选 | 如 `expression_matrix` | B | D Method |
| `keywords` | list[str] | 冻结 / 可选 | 关键词 | B | 检索 query |
| `notes` | list[str] | 冻结 / 可选 | 备注 | B | 调试 |
| `authors` | str | 冻结 / 可选 | 作者行 | UI/Brief | title_page / Back Matter |
| `ethics_statement` | str | 冻结 / 可选 | 成稿级伦理整段（若有则覆盖槽位拼接） | 临床/Brief | Methods / Back Matter |
| `ethics_committee` | str | V1.1 可选 | 伦理委员会全称 | 临床/Brief | Methods Participants |
| `ethics_approval_id` | str | V1.1 可选 | 批件号（如 ZS-824） | 临床/Brief | Methods Participants |
| `ethics_approval_date` | str | V1.1 可选 | 批准日期 | 临床/Brief | Methods Participants |
| `informed_consent_form` | str | V1.1 可选 | 同意形式（written…） | 临床/Brief | Methods Participants |
| `informed_consent_from` | str | V1.1 可选 | 签署方 | 临床/Brief | Methods Participants |
| `informed_consent_process` | str | V1.1 可选 | 告知与签署过程 | 临床/Brief | Methods Participants |
| `helsinki_declaration` | bool | V1.1 可选 | 是否声明遵循赫尔辛基宣言 | 临床/Brief | Methods Participants |
| `helsinki_citation` | str | V1.1 可选 | 宣言文献标记（如 `[20]`） | 临床/Brief | Methods Participants |
| `sample_source` | str | V1.1 可选 | 样本/队列来源 | 临床/Brief | Methods Participants overview |
| `study_design` | str | V1.1 可选 | 研究设计类型 | 临床/Brief | Methods Participants overview |
| `inclusion_criteria` | list[str] | V1.1 可选 | 纳入标准条目 | 临床/Brief | Methods Participants eligibility |
| `exclusion_criteria` | list[str] | V1.1 可选 | 排除标准条目 | 临床/Brief | Methods Participants eligibility |
| `n_participants` | int \| null | V1.1 可选 | 最终分析样本量 | 临床/Brief | Methods Participants overview |
| `n_participants_note` | str | V1.1 可选 | N 对应的数据完备条件 | 临床/Brief | Methods Participants overview |
| `power_analysis` | str | V1.1 可选 | 效能/灵敏度说明句 | 临床/Brief | Methods Participants overview |
| `clinical_assessment_lead_in` | str | V1.1 可选 | 临床量表小节总起句 | 临床/Brief | Methods Clinical assessment |
| `clinical_scales` | list[ClinicalScale] | V1.1 可选 | 量表镜像（`AnalysisBundle.clinical_scales` 优先） | 临床/Brief | Methods Clinical assessment |
| `author_contributions` | str | V1.1 可选 | CRediT 作者贡献成稿 | 用户/UI | Back Matter |
| `funding` | str | V1.1 可选 | 资助声明 | 用户/UI | Back Matter |
| `acknowledgments` | str | V1.1 可选 | 致谢 | 用户/UI | Back Matter |
| `conflicts_of_interest` | str | V1.1 可选 | 利益冲突声明 | 用户/UI | Back Matter |
| `data_accession` | str | V1.1 可选 | 数据登录号（如 CRA044389） | 用户/分析 | Back Matter |
| `data_repository` | str | V1.1 可选 | 数据仓库名 | 用户/分析 | Back Matter |
| `data_repository_url` | str | V1.1 可选 | 仓库 URL | 用户/分析 | Back Matter |
| `abbreviations` | list[AbbreviationEntry] | V1.1 可选 | 用户确认的缩写表 | 用户/UI | Back Matter Abbreviations |
| `data_availability` | str | 冻结 / 可选 | 数据可用性 | 同上 | Back Matter |
| `code_availability` | str | 冻结 / 可选 | 代码可用性 | 同上 | Back Matter |

详槽位说明见 [METHODS_PARTICIPANTS_SLOTS.md](METHODS_PARTICIPANTS_SLOTS.md)、[METHODS_PARTICIPANTS_ETHICS_SLOTS.md](METHODS_PARTICIPANTS_ETHICS_SLOTS.md)、[METHODS_REMAINING_SLOTS.md](METHODS_REMAINING_SLOTS.md)、[BACK_MATTER_SLOTS.md](BACK_MATTER_SLOTS.md)。

样例：`fixtures/paper_brief.json`  
端口：`BriefPort.load`

---

## 8. SectionInput（单章输入）

| 字段 | 类型 | 状态 | 含义 | 生产者 | 消费者 |
|------|------|------|------|--------|--------|
| `run_id` | str | 冻结 / 必填 | 本次写作 run | E orchestrator | 各章 |
| `section` | SectionId | 冻结 / 必填 | 目标章节 | E | 各章 |
| `brief` | PaperBrief | 冻结 | 课题简介 | E 注入 | C/D |
| `analysis` | AnalysisBundle \| null | 冻结 | 分析包 | E 注入 | D/E |
| `literature` | list[LiteratureHit] | 冻结 | 文献列表 | E 注入 | C |
| `payload` | dict | 冻结 / 可选 | 章内扩展参数（语言、开关等） | 调用方 | 各章 |

---

## 9. SectionDraft（单章输出）

| 字段 | 类型 | 状态 | 含义 | 生产者 | 消费者 |
|------|------|------|------|--------|--------|
| `section` | SectionId | 冻结 / 必填 | 章节 | 各章 | E/F |
| `title` | str | 冻结 / 建议必填 | 显示标题 | 各章 | F |
| `markdown` | str | 冻结 / 建议必填 | 正文 | 各章 | F、板块5 |
| `claims` | list[Claim] | 冻结 | 论断 | 各章 | E PaperState |
| `citations` | list[Citation] | 冻结 | 引用 | C 为主 | E/F |
| `figure_refs` | list[FigureRef] | 冻结 | 图表 | D 为主 | E/F |
| `warnings` | list[str] | 冻结 | 缺数/降级说明 | 各章 | F 汇总 |
| `metadata` | dict | 冻结 / 可选 | 模板名、计数等 | 各章 | 调试 |

**阶段1验收：** `markdown` 非空；Related 应有 `citations`；Result 应有带 `evidence_ids` 的 `claims`（允许与 fixtures 对齐的弱绑定）。

---

## 10. PaperState（跨章状态）

| 字段 | 类型 | 状态 | 含义 | 生产者 | 消费者 |
|------|------|------|------|--------|--------|
| `run_id` | str | 冻结 / 必填 | run id | E | 全组 |
| `brief` | PaperBrief | 冻结 | 课题 | E | 各章 |
| `confirmed_claims` | list[Claim] | 冻结 | 已确认论断累积 | E.`add_draft` | Conclusion |
| `citations` | list[Citation] | 冻结 | 引用累积 | E | F |
| `figure_refs` | list[FigureRef] | 冻结 | 图表累积 | E | F |
| `limitations` | list[str] | 冻结 | 局限 | E（可来自 analysis） | Conclusion |
| `section_order` | list[SectionId] | 冻结 | 运行顺序 | E | E |
| `drafts` | dict[str, SectionDraft] | 冻结 | 各章草稿 | E | F |

默认顺序：`introduction → related_work → data_method → result → conclusion`（冻结）。

---

## 11. WritingBundle（导出给可视化）

| 字段 | 类型 | 状态 | 含义 | 生产者 | 消费者 |
|------|------|------|------|--------|--------|
| `run_id` | str | 冻结 / 必填 | run id | F export | 板块5 |
| `brief` | PaperBrief | 冻结 | 课题 | F | 板块5 |
| `sections` | list[SectionDraft] | 冻结 / 建议必填 | 五章 | F | 板块5 |
| `claims` | list[Claim] | 冻结 | 汇总论断 | F | 板块5 |
| `citations` | list[Citation] | 冻结 | 汇总引用 | F | 板块5 |
| `figure_refs` | list[FigureRef] | 冻结 | 汇总图表 | F | 板块5 |
| `warnings` | list[str] | 冻结 | 汇总警告 | F | 板块5 |

落盘：`outputs/<run>/writing_bundle.json`，另附 `sections/*.md`、`paper_state.json`。

**给可视化组：** 主读 `writing_bundle.json`；用 `figure_refs.path` 找图；用 `citations` 生成参考文献列表。

---

## 12. 延期到阶段 2+ 的项（V1 不纳入新字段）

| 提案 | 说明 | 目标阶段 | 状态 |
|------|------|----------|------|
| Claim 强制非空 evidence | 无证据则 warn/degraded 或 strict 失败；**不新增字段**，结果进 `warnings` + `metadata.evidence_binding` | 2 | **已落地**（`evidence/` + pipeline） |
| ReAct trajectory 对象 | Thought/Action/Observation 落盘（`react/` + `metadata.react` + `react_trajectory.json`）；**不新增 V1 字段** | 3 | **已落地** |
| PaperState 一致性强化 | 跨章检查 + 聚合重建/局限去重；报告进 `consistency_report.json` / `metadata.consistency` | 4 | **已落地** |
| LLM 生成元数据 | `SectionDraft.metadata.llm`（provider/model/polished；无新 V1 字段） | 5 | **已落地**（默认关；`--llm` 开启） |
| 自动 outline 生成 | 替代固定五章 | 4（可选） | 延期（本阶段未做自动大纲） |
| live adapter 配置块 | 服务 URL 等 | 5 | 延期 |

阶段 2 策略摘要：Introduction 允许空 `evidence_ids`；Related / Data & Method / Result / Conclusion 要求非空且可解析。详见 [PHASE2_TASKS.md](../PHASE2_TASKS.md)。  
阶段 3：Related Work 默认启用确定性 ReAct（`LiteraturePort.search/get`），见 [PHASE3_TASKS.md](../PHASE3_TASKS.md)。  
阶段 4：见 [PHASE4_TASKS.md](../PHASE4_TASKS.md)。

---

## 13. 成员 A 日常职责（接口法官）

1. 审查任何触及 `contracts.py` 的 PR  
2. 维护本文与 [SCHEMA_CHANGELOG.md](SCHEMA_CHANGELOG.md)  
3. 对外（文献/分析/可视化）只发 **本文 + fixtures 样例**，不发口头字段  
4. 发现章节使用未登记字段 → 打回或走变更流程  
5. 阶段1结束时确认：fixtures 可校验、导出包字段 ⊆ V1

阅读导读：[REFERENCES_PHASE1.md](REFERENCES_PHASE1.md) · 术语：[TERMINOLOGY.md](TERMINOLOGY.md)
