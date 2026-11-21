"""paragraph-writer: write or revise one planned paragraph.

The caller passes the model as ``payload['model']`` (an object with
``generate(prompt, system=None)``). The skill owns the prompt: one paragraph,
its own facts, its allowed interpretations and its prohibitions. Revision mode
receives the previous draft and the verifier's issues and fixes only those.
"""

from __future__ import annotations

import re
from typing import Any

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from registry import register_skill


SYSTEM = (
    "You write one paragraph of a scientific research paper in clear academic English. "
    "Output only the paragraph as plain prose: no heading, no list, no Markdown, no quotation marks around it."
)


def _bullets(items) -> str:
    return "\n".join(f"- {item}" for item in items) or "- (none)"


def build_prompt(paragraph: dict[str, Any], *, section: str, title: str, previous: str = "",
                 draft: str = "", issues: list[str] | None = None, style_rules: str = "") -> str:
    parts = [
        f"Paper title: {title or '(untitled)'}",
        f"Section: {section}",
        f"Paragraph purpose: {paragraph['purpose']}",
        "Facts you may use. Copy every number exactly as written, with its unit and sample name:\n"
        + _bullets(paragraph.get("facts", [])),
        "Interpretations you may state (you may reword them, but do not go beyond them):\n"
        + _bullets(paragraph.get("notes", [])),
        "Rules:\n" + _bullets(paragraph.get("forbid", [])),
    ]
    if style_rules.strip():
        parts.append("Style guidance from the selected writing skills (the rules above take priority):\n"
                     + style_rules.strip()[:12000])
    if previous:
        parts.append(f'The previous paragraph ends with: "{previous}" Do not repeat it.')
    if draft:
        parts.append("Your previous version of this paragraph:\n" + draft)
        parts.append("A checker reported these problems:\n" + _bullets(issues or []) +
                     "\nFix each real problem. A problem about a number, an identifier, a word to avoid or a bracketed "
                     "id is always real. If another reported problem is wrong because the facts above do state the "
                     "point, keep that sentence unchanged. Never replace a name or number with one that is not in the facts.")
        parts.append(f"Write the corrected paragraph in {paragraph['min_words']}-{paragraph['max_words']} words.")
    else:
        parts.append(f"Write the paragraph in {paragraph['min_words']}-{paragraph['max_words']} words. "
                     "Use only the facts and interpretations above.")
    return "\n\n".join(parts)


def clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    text = re.sub(r"^\s*#+\s.*$", "", text, flags=re.M)
    text = text.replace("**", "").strip().strip('"').strip()
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def last_sentence(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text.strip())
    return sentences[-1] if sentences and sentences[-1] else ""


@register_skill
class ParagraphWriterSkill(BaseSkill):
    spec = SkillSpec(
        name="paragraph-writer",
        version="1.0.0",
        category=SkillCategory.WRITING,
        description="按段落计划逐段写作或按核对意见修改单段；每次只给模型本段的事实、可写解读和禁令。",
        tags=("writing", "drafting", "llm"),
        input_keys=("model", "paragraph", "section", "title", "previous", "draft", "issues", "style_rules"),
        output_keys=("text", "prompt_chars"),
    )

    def run(self, skill_input: SkillInput):
        payload = skill_input.require("model", "paragraph", "section")
        model = payload["model"]
        prompt = build_prompt(payload["paragraph"], section=str(payload["section"]),
                              title=str(skill_input.get("title") or ""),
                              previous=str(skill_input.get("previous") or ""),
                              draft=str(skill_input.get("draft") or ""),
                              issues=list(skill_input.get("issues") or []),
                              style_rules=str(skill_input.get("style_rules") or ""))
        text = clean(model.generate(prompt, system=SYSTEM))
        if not text:
            raise ValueError("model returned an empty paragraph")
        return self.success(data={"text": text, "prompt_chars": len(prompt)},
                            summary=f"{payload['paragraph']['key']}: {len(text.split())} words")
