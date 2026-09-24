# Results · 负面写作约束提示词

对照写作经验 PDF「Results 正文不应该写什么」。

**不做槽位**：这是润色约束，不是研究特异事实字段。

实现：`article_writing/prompts/results.py` → `RESULTS_NEGATIVE_CONSTRAINTS_PROMPT`  
挂载：`ResultsSection.metadata.llm_instructions_extra`（pipeline 润色时自动并入）

## 四条硬约束

1. 不写大段背景 → Introduction  
2. 不写详细方法原理 → Methods  
3. 不写过多机制解释 → Discussion  
4. 不重复图表全部内容（菌种/通路/p 值逐条罗列）→ 正文概括主模式，细节看图

另：保留数字、证据 id、插图 `![…](…)`；中英双语；禁止编造。
