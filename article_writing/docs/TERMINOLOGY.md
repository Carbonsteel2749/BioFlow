# 术语对照表（外部项目 ↔ BioFLow article_writing）

> 组内沟通请优先使用**右列「我们的说法」**。  
> 真源代码：`article_writing/contracts.py`（V1 冻结说明见 [CONTRACTS_V1.md](CONTRACTS_V1.md)）。

---

## 总览

| 外部说法（PaperQA / STORM / 常见 RAG） | 我们的说法 | 落点 |
|----------------------------------------|------------|------|
| Retrieved paper / hit / document | 文献命中 | `LiteratureHit` |
| Evidence / supporting context | 证据 ID / 证据绑定 | `Claim.evidence_ids`；分析侧见 `AnalysisBundle.evidence_ids` |
| Citation / in-text reference | 引用 | `Citation` |
| Claim / answer statement | 论断 | `Claim` |
| Query → search → answer | 文献端口检索 | `LiteraturePort.search` → `List[LiteratureHit]` |
| Gather evidence then generate | 先证据后写作（阶段2强化） | `evidence/` + 各 `SectionDraft.claims` |
| Pre-writing / research stage | 预写作（收集 brief/文献/分析包） | fixtures + adapters + `SectionInput` |
| Outline | 章节顺序 / 五章大纲 | `SectionId` + `PaperState.section_order` |
| Section draft | 章节草稿 | `SectionDraft` |
| Article state / shared memory | 全文状态 | `PaperState` |
| Final article export | 写作导出包 | `WritingBundle` + `sections/*.md` |
| Figure / table asset | 图表引用 | `FigureRef` |
| Topic / research brief | 课题简介 | `PaperBrief` |
| Analysis result package | 分析结论包 | `AnalysisBundle` |
| Agent trajectory (ReAct) | 轨迹（阶段3） | `react/`（占位） |

---

## PaperQA 细映射

| PaperQA README 概念 | 我们怎么说 | 阶段建议 |
|---------------------|------------|----------|
| Paper Search | `LiteraturePort.search` | 1=mock；5=live |
| Gather Evidence | 为 `Claim` 填 `evidence_ids`，并为文献填 `Citation.snippet` | 2 强化 |
| Generate Answer | `BaseSection.run` → `SectionDraft.markdown` | 1=模板；后期可 LLM |
| In-text citations | `Citation.cite_id` + 正文中引用该 id | Related 优先 |
| Local PDF corpus RAG | 对接文献板块全文库（非本阶段） | 5+ |

**注意：** PaperQA 回答的是「文献问题」；我们的 **Result** 回答的是「本次分析包里的发现」。不要把 DEG 数字交给纯文献 RAG 去「编」。

---

## STORM 细映射

| STORM README 概念 | 我们怎么说 | 阶段建议 |
|-------------------|------------|----------|
| Pre-writing（检索+提问） | 组装 `SectionInput`（brief + literature + analysis） | 1 用 fixtures |
| Perspective-guided questions | Related/Intro 的检索 query 设计（概念） | 3 |
| Outline generation | 固定五章 `section_order`（阶段1不自动生成大纲） | 1 固定；4 可扩展 |
| Article generation | 五章 `sections/*` | 全程 |
| Article polishing | 导出后润色 / 可视化排版 | 板块5 + 后期 |
| Citations in long article | `PaperState.citations` → `WritingBundle.citations` | 1 起收集 |

**注意：** STORM 面向百科式长文；我们 **Data & Method / Result** 以 `AnalysisBundle` 为真源，不是互联网检索真源。

---

## 章节用语（组内统一英文 id）

| 中文 | `SectionId` | 主要输入 |
|------|-------------|----------|
| 引言 | `introduction` | `PaperBrief` |
| 相关工作 | `related_work` | `literature: List[LiteratureHit]` |
| 数据与方法 | `data_method` | `AnalysisBundle.methods` |
| 结果 | `result` | `key_findings` / `metrics` / `figure_refs` |
| 结论 | `conclusion` | `PaperState.confirmed_claims` + limitations |

禁止再用旧骨架里的 `discussion` / `abstract` 当本板块正式章节名（那些属于历史 `bioflow/modules/writing`，与本板块 V1 无关）。

---

## 禁止混用的说法

| 别再说 | 请改说 |
|--------|--------|
| 「把 STORM 跑一下出结果章」 | 「Result 渲染 AnalysisBundle」 |
| 「evidence 就是整篇 PDF」 | 「evidence_id 是可追溯标识；全文在文献板块」 |
| 「citation 随便写个作者名」 | 「必须有 `cite_id`，尽量有 doi/pmid/snippet」 |
| 「bundle 就是 markdown」 | 「`WritingBundle` JSON + 各章 md；可视化读 JSON」 |
