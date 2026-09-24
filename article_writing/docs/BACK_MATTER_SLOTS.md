# Back Matter · 行政槽位 + Abbreviations

对照钙–ASD 范文文后：Author Contributions、Funding、IRB、Informed Consent、Data Availability、Acknowledgments、Conflicts of Interest，以及 Abbreviations。

## 行政块（槽位 + 用户填写，禁止编造）

| 范文小节 | PaperBrief 字段 |
|----------|-----------------|
| Author Contributions | `author_contributions`（可辅以 `authors`） |
| Funding | `funding` |
| IRB Statement | 复用 `ethics_statement` 或 `ethics_committee` / `ethics_approval_id` / `ethics_approval_date` / `helsinki_*` |
| Informed Consent | 复用 `informed_consent_*` |
| Data Availability | `data_availability` 或 `data_repository` + `data_accession` + `data_repository_url` |
| Code Availability | `code_availability`（范文可无，槽位保留） |
| Acknowledgments | `acknowledgments` |
| Conflicts of Interest | `conflicts_of_interest` |

缺槽 → 占位句 + `warnings` / `metadata.user_input_needed`（向用户索要清单）。

实现：`prompts/back_matter.py` · `sections/back_matter.py`  
润色：`BACK_MATTER_ADMIN_LLM_PROMPT`

## Abbreviations（种子 + LLM 建议）

1. **用户确认优先**：`PaperBrief.abbreviations` → `[{abbreviation, expansion}, …]`  
2. **确定性种子**：从临床量表缩写、暴露检测方法 `Full (ABBR)`、统计/测序槽位中的 `Full (ABBR)` 收获  
3. **LLM**：`ABBREVIATIONS_LLM_PROMPT` — 仅当缩写与全称已出现在本稿中才可增补；标记为 suggested，需作者确认  

不根据「领域常识」凭空补全称。
