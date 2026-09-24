# Discussion · 结构润色提示词

对照写作经验 PDF §4.6（概括主发现 → 分层解释 → 文献对话 → 局限与展望）。

**不做新槽位**：发现/局限来自 `AnalysisBundle` + Results claims；文献来自 `LiteraturePort`。提示词约束润色叙事结构与写作原则。

实现：`article_writing/prompts/discussion.py` → `DISCUSSION_STRUCTURE_PROMPT`  
挂载：`DiscussionSection.metadata.llm_instructions_extra`

## 结构映射

| 指南 | 模板小节 | 提示词要求 |
|------|----------|------------|
| 4.6.1 第一段概括主发现 | Interpretation of principal findings | 短段落复述核心模式；可点出阴性结果 |
| 4.6.2 中间分层解释 | Mechanisms and literature context（前半） | 表型 / 整体 vs 物种 / 通路 / 整合；基于数据；勿过度外推机制 |
| 4.6.3 与文献对话 | 同上（文献列表） | 明确一致 / 不一致 / 可能原因 |
| 4.6.4 局限+展望 | Limitations + Outlook | 局限具体；展望简短；结论留给 Conclusions |

硬约束：不编造数值/菌种/引用；中英双语；Conclusions 不并入 Discussion。
