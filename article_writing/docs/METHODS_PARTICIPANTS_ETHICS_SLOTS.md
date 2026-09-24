# Methods · Participants · 伦理/知情同意槽位

对照范文：钙–ASD 论文 `Materials and Methods → 2.1. Participants` 第二段，以及写作经验 PDF 例段。

## PaperBrief 槽位（上游填写，禁止模型编造）

| 槽位 | 字段 | 范文取值示例 |
|------|------|----------------|
| 伦理委员会 | `ethics_committee` | Institutional Review Board of Peking Union Medical College Hospital |
| 批件号 | `ethics_approval_id` | ZS-824（导出为 `IRB #ZS-824`） |
| 批准日期 | `ethics_approval_date` | 12 October 2024（可选） |
| 同意形式 | `informed_consent_form` | written informed consent |
| 签署方 | `informed_consent_from` | a parent or legal guardian |
| 告知过程 | `informed_consent_process` | received detailed information… before providing consent |
| 赫尔辛基 | `helsinki_declaration` + `helsinki_citation` | true + `[20]` |
| 整段覆盖 | `ethics_statement` | 若已有成稿整段，优先使用，不再拼槽位 |

## 模板句式（与范文同构）

1. The study was approved by the {committee} ({IRB id}[, approved on {date}]).  
2. {consent form} was obtained from {consent from}.  
3. {consent from} {process}[, in accordance with the Declaration of Helsinki {cite}].

实现：`article_writing/prompts/methods_participants_ethics.py`  
写入 Methods：`## Participants` → `### Ethics approval and informed consent`  
润色：`SectionDraft.metadata.llm_instructions_extra`（pipeline 自动并入）

## LLM 提示词

见同文件 `ETHICS_CONSENT_LLM_PROMPT`：只润色该块、中英双语、**不得编造**批号/机构/同意事实。
