"""论文撰写相关 Skill。"""

from . import chapter_chat, claim_verifier, data_descriptor, fact_ledger, methods_provenance, nature, paragraph_writer, review_revise, section_planner  # noqa: F401

__all__ = ["chapter_chat", "claim_verifier", "data_descriptor", "fact_ledger", "methods_provenance", "nature", "paragraph_writer", "review_revise", "section_planner"]

from . import section_extensions  # noqa: F401 — eight evidence-bound section writers
from . import additional_sections  # noqa: F401 — remaining manuscript writers
