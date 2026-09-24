"""Discussion section LLM polish prompt (writing-guide §4.6 structure)."""

from __future__ import annotations

DISCUSSION_STRUCTURE_PROMPT = """
You are polishing ONLY the Discussion section of a bioinformatics / biomedical paper.

Follow this journal structure (align headings if present; do not invent new Results):

1) Opening paragraph — summarize MAIN findings only
   - Restate the core result pattern from the draft (e.g. exposure ↔ phenotype,
     taxa, pathways) in a few sentences.
   - Note null / negative findings when the draft reports them (e.g. diversity
     unchanged while specific taxa/pathways differ).
   - Mention integrative links only if already present in Results/draft claims.
   - Do NOT dump Methods details or full metric tables here.

2) Middle paragraphs — interpret results by LAYER (data-based)
   Organize interpretation around the layers present in the draft, for example:
   - exposure / key variable ↔ behavioral (or clinical) phenotypes;
   - overall microbiome structure vs specific taxon differences;
   - functional / metabolic pathway differences and their plausible meaning;
   - integrated associations among taxa, pathways, and behavior.
   Principles:
   - Explanations must be grounded in supplied findings, metrics, and literature.
   - Connect to existing literature when hits/citations are provided.
   - Give reasonable accounts for inconsistent or null results when relevant.
   - Do NOT over-extrapolate underlying mechanisms or claim causality without
     support in the draft / paper context.

3) Dialogue with prior literature
   Explicitly clarify, when literature is available:
   - which results are CONSISTENT with prior studies;
   - which results DIFFER from prior studies;
   - what factors might explain inconsistencies (design, cohort, assay, stats),
     without inventing unstated study details.

4) Closing — Limitations + brief future outlook
   - Keep Limitations concrete and based on supplied limitation bullets / draft.
   - Keep Outlook short; detailed take-home conclusions belong in Conclusions
     (do not merge a full Conclusions section into Discussion).

HARD RULES:
- Use ONLY facts from the draft + immutable paper context + listed literature.
- Do not invent numbers, taxa, pathways, citations, or mechanisms.
- Preserve citation keys (lit_*), evidence ids, and any embeds exactly.
- Output bilingual English + 中文 under ## English / ## 中文.
- Output ONLY Markdown.
""".strip()
