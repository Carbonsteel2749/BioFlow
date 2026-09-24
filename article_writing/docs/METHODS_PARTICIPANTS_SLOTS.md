# Methods · Participants 全套槽位

对照范文：钙–ASD 论文 `2.1. Participants` + 写作经验 PDF。

## 结构（已落地）

1. **Study overview**：样本来源、研究设计、最终 N（+完备条件）、可选效能句  
2. **Eligibility criteria**：纳入标准、排除标准  
3. **Ethics approval and informed consent**：伦理委员会、批号、知情同意、赫尔辛基  

## PaperBrief 槽位

| 项目 | 字段 |
|------|------|
| 研究设计类型 | `study_design` |
| 样本来源 | `sample_source` |
| 最终样本量 | `n_participants` |
| N 的数据完备说明 | `n_participants_note` |
| 效能/灵敏度（可选） | `power_analysis` |
| 纳入标准 | `inclusion_criteria: list[str]` |
| 排除标准 | `exclusion_criteria: list[str]` |
| 伦理/知情同意 | 见同目录伦理槽位表 / `METHODS_PARTICIPANTS_ETHICS_SLOTS.md` |

## 代码

- Overview/eligibility：`article_writing/prompts/methods_participants_overview.py`  
- Ethics：`article_writing/prompts/methods_participants_ethics.py`  
- 写入：`sections/methods.py` → `## Participants`  
- 润色：`metadata.llm_instructions_extra`（overview + eligibility + ethics 合并）

**规则：** 槽位由上游 Brief/临床模块填写；LLM 只按例段框架润色，不得编造 N、纳入排除条目或伦理批号。
