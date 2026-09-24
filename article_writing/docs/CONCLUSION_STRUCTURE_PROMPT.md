# Conclusions · 三问结构提示词

对照写作经验 PDF §4.7：Conclusion 应简短、准确，通常回答三个问题。

**不做新槽位**：结论条目来自上游 Results/Methods claims；局限来自 analysis / PaperState。

实现：`article_writing/prompts/conclusion.py` → `CONCLUSION_STRUCTURE_PROMPT`  
挂载：`ConclusionSection.metadata.llm_instructions_extra`

## 三问 ↔ 模板小节

| 问题 | 模板小节 |
|------|----------|
| 本研究发现了什么？ | Principal conclusions |
| 这些发现提示了什么？ | Scientific and practical implications |
| 后续还需要什么研究？ | Limitations to keep in view + Closing statement |

硬约束：简短准确；不并入 Discussion；不编造数值/机制/临床建议；中英双语。
