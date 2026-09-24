"""Conclusions section LLM polish prompt (writing-guide §4.7)."""

from __future__ import annotations

CONCLUSION_STRUCTURE_PROMPT = """
You are polishing ONLY the Conclusions section of a bioinformatics / biomedical paper.

Tone: BRIEF and ACCURATE. Prefer short paragraphs or a few numbered points.
This section is STANDALONE and must NOT be merged into Discussion.

Answer exactly these three questions (map onto existing headings when present):

1) What did this study find?
   - Summarize the primary findings from the draft / upstream claims only.
   - No new numbers, taxa, pathways, or thresholds.
   - Do not re-expand full Results lists or re-discuss every figure.

2) What do these findings suggest?
   - State concise implications / scientific meaning that are already supported
     by the draft or immutable paper context.
   - Do not invent clinical recommendations, causal mechanisms, or policy claims.

3) What further research is needed?
   - Tie to supplied limitations and a short future-work outlook.
   - Keep this shorter than Discussion's Limitations/Outlook; do not duplicate
     a full literature debate here.

HARD RULES:
- Use ONLY facts from the draft + immutable paper context.
- Do not invent or alter numbers, citation keys, evidence ids, or conclusions.
- Keep the explicit note that Conclusions are not merged into Discussion.
- Output bilingual English + 中文 under ## English / ## 中文.
- Output ONLY Markdown.
""".strip()
