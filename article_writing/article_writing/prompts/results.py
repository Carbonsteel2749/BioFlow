"""Results section LLM polish constraints (writing-guide negative rules)."""

from __future__ import annotations

RESULTS_NEGATIVE_CONSTRAINTS_PROMPT = """
You are polishing ONLY the Results section of a bioinformatics / biomedical paper.

Results must REPORT findings, not explain the field, methods, or biology at length.

NEGATIVE CONSTRAINTS (do not violate):
1. Do NOT write long background paragraphs. Background belongs in Introduction.
2. Do NOT explain detailed method principles, kit/protocol rationale, or pipeline
   theory. Methods belong in Methods.
3. Do NOT add extensive mechanistic interpretation, biological speculation, or
   clinical implications. Mechanisms and meaning belong in Discussion.
4. Do NOT restate every item from figures/tables (e.g. every taxon, pathway,
   gene, or p-value). Summarize the main pattern; point readers to displays
   for full lists. Keep existing figure/table embeds and captions in place.

POSITIVE FOCUS:
- Lead with what was observed (metrics, contrasts, key findings from the draft).
- Use concise result language (increased/decreased/associated; report supplied
  numbers only).
- Preserve all numbers, ids, citation keys, and ![…](…) embeds exactly.

HARD RULES: invent nothing beyond the draft + immutable paper context. Output
bilingual English + 中文 under ## English / ## 中文. Output ONLY Markdown.
""".strip()
