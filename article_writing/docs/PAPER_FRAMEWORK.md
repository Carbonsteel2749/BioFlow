# 论文生成框架（权威生信论文对齐版）

本框架根据 ASD/菌群等多篇生信论文共有结构冻结，作为 `article_writing` **默认导出骨架**。  
**Conclusions 单独成章，不得并入 Discussion。**

## 固定顺序

1. **Title / Authors**（导出 `title_page.md`，来自 `PaperBrief`）  
2. **Abstract**（`abstract`）  
3. **Introduction**（`introduction`）：Background → Gap → Objective  
4. **Methods**（`methods`）：Participants → Clinical assessment → Exposure measurement → Microbiome sequencing → Statistical analysis（+ 可选 pipeline notes / 伦理交叉引用）  
5. **Results**（`results`）：指标概览 → 主发现分节 → 次要指标 → 图/表  
6. **Discussion**（`discussion`）：解释发现 → 文献/机制对照 → 局限 → 展望  
7. **Conclusions**（`conclusion`）：**独立结论**（不并入 Discussion）  
8. **Back Matter**（`back_matter`）：Author Contributions · Funding · IRB · Consent · Data/Code · Acknowledgments · Conflicts · Abbreviations · References · Supplementary  

> 期刊正文通常不设独立 Related Work；文献写入 Introduction / Discussion。  
> ReAct 检索仍为这两章提供文献命中。

## 各部分应写满的内容（对照权威论文）

| 部分 | 应覆盖的内容要点 |
|------|------------------|
| **Abstract** | Background/Objectives；Methods（模态+QC/归一化/差异阈值）；Results（发现+关键 metrics）；Conclusions；Keywords |
| **Introduction** | 表型/生物学背景段；数据模态与分析必要性；文献上下文（可引用多条）；Gap（定义/异质性/本题未闭合）；Objective（可操作条目 1–3） |
| **Methods** | **Participants**（overview + eligibility + ethics）；**Clinical assessment**；**Exposure measurement**；**Microbiome sequencing**；**Statistical analysis**（槽位见 `METHODS_*_SLOTS.md`） |
| **Results** | 分析快照与 metrics；主发现分节并尽量挂图；次要定量观察；图/表清单与题注；润色负面约束见 `RESULTS_NEGATIVE_CONSTRAINTS.md` |
| **Discussion** | 重述并解释主发现；与文献对照（一致/不一致）；机制假说不作无证据因果；局限；简短展望（详细结论留给 Conclusions）；润色结构见 `DISCUSSION_STRUCTURE_PROMPT.md` |
| **Conclusions** | 独立条目化结论；科学含义；收尾局限；收束句（可声明不并入 Discussion）；润色三问结构见 `CONCLUSION_STRUCTURE_PROMPT.md` |
| **Back Matter** | 行政槽位（向用户索要，见 `BACK_MATTER_SLOTS.md`）；Abbreviations 种子+LLM 建议确认；参考文献；补充材料路径 |

## 写作与输出约束（已落地）

1. **中英双语**：启用 `--llm` 后，各章输出 `## English` + `## 中文`（事实等价）。  
2. **期刊化贯连**：润色时携带上游 question / findings / metrics，要求 Methods↔Results↔Conclusions 叙事连贯。  
3. **禁止改数/改结论**：不可编造或改动其他模块给出的数据、结论、引用键、图片路径；锚点丢失则退回模板。  
4. **排版**：导出整篇 `manuscript.md` + 分章 `sections/*.md`。  
5. **插图落位**：Results 按发现插入 `![…](figures/…)`；导出时复制到 `figures/`。
