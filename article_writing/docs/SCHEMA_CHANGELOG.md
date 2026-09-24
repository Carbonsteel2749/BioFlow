# Schema Changelog

记录 `article_writing/contracts.py` 与契约文档的变更。  
格式：日期 | 版本 | 变更摘要 | 审批人 | 影响成员

---

## 2026-07-26 | V1 | 初始冻结

- 冻结模型：`SectionId`, `Claim`, `Citation`, `FigureRef`, `LiteratureHit`, `AnalysisBundle`, `PaperBrief`, `SectionInput`, `SectionDraft`, `PaperState`, `WritingBundle`
- 文档落地：`docs/CONTRACTS_V1.md`, `docs/TERMINOLOGY.md`, `docs/REFERENCES_PHASE1.md`
- **无字段重命名**（与当前代码一致）
- 审批人：成员 A（Phase-1 接口规范）
- 影响：全组按 V1 开发；改字段必须追加本日志新条目

---

## 2026-08-06 | V1（行为） | Phase-2 证据绑定（无新字段）

- 变更：落地 `evidence/` 校验与 `WritingPipeline(evidence_mode=...)`；绑定结果写入已有 `SectionDraft.warnings` / `metadata`
- 动机：Related/Result 等论断可追溯，为阶段 3/5 铺路
- 破坏性：否（默认 `warn`；`strict` 为可选）
- 需同步：tests / PHASE2_TASKS / CONTRACTS §12 状态
- 审批人：撰写模块负责人

---

## 2026-08-06 | V1（行为） | Phase-3 Related Work ReAct（无新 V1 字段）

- 变更：落地 `react/` 确定性循环；轨迹写入 `metadata.react` 与 `react_trajectory.json`；默认 `react_enabled=True`
- 动机：Related 多步取证（学 PaperQA/STORM 流程，不引入其依赖）
- 破坏性：否（可用 `--no-react` 回退单次 search）
- 需同步：tests / PHASE3_TASKS / CONTRACTS §12 / example.yaml
- 审批人：撰写模块负责人

---

## 2026-08-06 | V1（行为） | Phase-4 PaperState 一致性（无新 V1 字段）

- 变更：落地 `consistency/`；pipeline 末尾 harden；导出 `consistency_report.json`
- 动机：跨章聚合/局限/结论引用可检查，便于阶段 5 接入
- 破坏性：否（默认 `warn`；`strict` 可选）
- 需同步：tests / PHASE4_TASKS / CONTRACTS §12
- 审批人：撰写模块负责人

---

## 2026-08-31 | V1.1 | IMRaD 论文框架对齐（章节枚举变更）

- 变更：`SectionId` 改为 `abstract/introduction/methods/results/discussion/conclusion/back_matter`；移除独立 `related_work`；`PaperBrief` 增加可选 `authors/ethics_statement/data_availability/code_availability`
- 动机：对齐权威生信论文共有结构；**Conclusions 强制独立**
- 破坏性：是（旧五章 id 与导出文件名变更）
- 需同步：sections / pipeline / tests / README / PAPER_FRAMEWORK
- 审批人：撰写模块负责人

---

## 2026-08-31 | V1（行为） | LLM 润色接入（无新 V1 字段）

- 变更：落地 `article_writing/llm/`（Ollama / OpenAI-compat / Fake）；pipeline 在模板草稿后可选润色；元数据写入 `SectionDraft.metadata.llm`
- 动机：在无法接真分析/文献模块时提升可读性；事实仍由模板与合约约束，禁止模型编造数值
- 破坏性：否（默认 `llm.enabled=false`；CLI `--llm` 开启）
- 推荐模型：本地 **Ollama + qwen3:14b**
- 需同步：tests / README / configs/example.yaml / CONTRACTS §12
- 审批人：撰写模块负责人

---

## 2026-08-31 | V1（行为） | 双语润色 + 插图排版（无新 V1 字段）

- 变更：默认写作要求改为中英双语；Results 内联插入上游图；导出 `manuscript.md` + `figures/` + `layout_report.json`；polish 携带 paper_context 贯连方法/结果/结论且禁止改数
- 动机：落实期刊化输出、插图落位与防幻觉约束
- 破坏性：否（默认仍可无 LLM 导出英文模板稿 + 插图排版）
- 需同步：tests / README / fixtures SVG
- 审批人：撰写模块负责人

---

## 2026-09-07 | V1.1（字段） | Participants 伦理/知情同意槽位

- 变更：`PaperBrief` 增加可选 `ethics_committee/ethics_approval_id/ethics_approval_date/informed_consent_* /helsinki_*`；Methods 增加 `## Participants` 伦理块；提示词见 `prompts/methods_participants_ethics.py`
- 动机：对齐钙–ASD 范文与写作指南例段；槽位由上游填充，LLM 只润色不编造
- 破坏性：否（均为可选默认空）
- 需同步：tests / CONTRACTS §7 / METHODS_PARTICIPANTS_ETHICS_SLOTS.md
- 审批人：撰写模块负责人

---

## 2026-09-07 | V1.1（字段） | Participants overview/eligibility 槽位

- 变更：`PaperBrief` 增加 `sample_source/study_design/inclusion_criteria/exclusion_criteria/n_participants/n_participants_note/power_analysis`；Methods Participants 增加 Study overview + Eligibility；提示词见 `prompts/methods_participants_overview.py`
- 动机：补齐写作指南 Participants 五项（来源/设计/纳入/排除/N）与范文第一段
- 破坏性：否（可选字段）
- 需同步：tests / CONTRACTS §7 / METHODS_PARTICIPANTS_SLOTS.md
- 审批人：撰写模块负责人

---

## 2026-09-07 | V1.1（字段） | Methods 剩余四段槽位（临床/暴露/测序/统计）

- 变更：新增 `ClinicalScale` / `ExposureMeasurement` / `MicrobiomeSequencing` / `StatisticalAnalysisPlan`；`AnalysisBundle` 增加对应可选字段；`PaperBrief` 增加 `clinical_assessment_lead_in` + `clinical_scales` 镜像；Methods 小节改为 Clinical assessment → Exposure measurement → Microbiome sequencing → Statistical analysis；提示词见 `prompts/methods_remaining.py`
- 动机：对齐写作指南 §4.4.2–4.4.5 与钙–ASD 范文；研究特异细节由上游填槽，LLM 只润色不编造
- 破坏性：否（可选字段；旧 `methods` dict 仍作 pipeline notes / 统计回退）
- 需同步：tests / CONTRACTS §6–7 / METHODS_REMAINING_SLOTS.md / PAPER_FRAMEWORK
- 审批人：撰写模块负责人

---

## 2026-09-07 | V1.1（字段） | Back Matter 行政槽位 + Abbreviations

- 变更：新增 `AbbreviationEntry`；`PaperBrief` 增加 `author_contributions/funding/acknowledgments/conflicts_of_interest/data_accession/data_repository/data_repository_url/abbreviations`；Back Matter 按期刊行政小节渲染；Abbreviations 确定性种子 + LLM 抽取提示；`back_matter` 纳入默认润色集合
- 动机：对齐范文文后声明；行政事实向用户索要、禁止编造；缩写可从槽位/正文收获并待确认
- 破坏性：否（可选字段）
- 需同步：tests / CONTRACTS §7 / BACK_MATTER_SLOTS.md / PAPER_FRAMEWORK
- 审批人：撰写模块负责人

---

## 模板（下次变更复制）

```text
## YYYY-MM-DD | V1.x | 标题

- 变更：
- 动机：
- 破坏性：是/否
- 需同步：fixtures / sections / tests / 外组
- 审批人：
```
