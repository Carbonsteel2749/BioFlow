"""Chapter chat: revise one language of a chapter on the author's free-form request.

``chapter-chat-reviser`` (model, JSON + code checks). The model sees only the language half
the author is editing, plus the recent conversation, and returns ``{reply, revised}``.
The rewrite is applied only if it

* keeps every number, sample/run identifier, citation marker and figure/table line,
* adds no new number, identifier or citation,
* keeps author/skill placeholders,
* stays in the requested language and is not wildly longer.

One retry is made with the violations spelled out; if it still fails, the text is kept
and the reasons are reported. A rewrite that changes nothing is reported as such, never
as a success. The other language half is never touched.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any

try:  # 标准包调用
    from ...core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from ...registry import register_skill
except ImportError:  # 兼容早期顶层脚本调用
    from core import BaseSkill, SkillCategory, SkillInput, SkillSpec
    from registry import register_skill

from .claim_verifier import citation_keys

# Numbers inside Chinese text sit right after CJK characters, so only letters/digits block a match.
NUMBER = re.compile(r"(?<![A-Za-z0-9.,])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![A-Za-z0-9.,])\d+(?:\.\d+)?")
IDENTIFIER = re.compile(r"\b(?:[EDS]R[RSPXA]|PRJ[EDN][A-Z]?|GC[AF]_)\d+(?:\.\d+)?\b")
ASSET = re.compile(r"^\s*(?:!\[|\*\*(?:Figure|Table) \d+\.\*\*)")
PLACEHOLDER = re.compile(r"\[(?:AUTHOR_INPUT_NEEDED|SKILL_PENDING):?[^\]]*\]")
CJK = re.compile(r"[一-鿿]")
LATIN = re.compile(r"[A-Za-z]")

MARK_EN, MARK_ZH, MARK_SHARED = "<!-- lang:en -->", "<!-- lang:zh -->", "<!-- lang:shared -->"
ZH_LABEL = re.compile(r"^[ \t]*(?:#{1,6}[ \t]*)?中文[ \t]*$", re.M)

SYSTEM = (
    "You are the author's editing assistant for one section of a scientific manuscript. "
    "You change only what the author asks for and never add facts."
)
SCHEMA = {
    "type": "object",
    "properties": {"reply": {"type": "string"}, "revised": {"type": "string"}},
    "required": ["reply", "revised"],
}


# ---------- language halves (same rules as frontend/writing-studio.js) ----------
def split_languages(markdown: str) -> dict[str, str]:
    text = markdown or ""
    if MARK_EN in text and MARK_ZH in text:
        after = text.split(MARK_EN, 1)[1]
        en, rest = after.split(MARK_ZH, 1)
        zh, _, shared = rest.partition(MARK_SHARED)
        return {"mode": "marked", "en": en.strip(), "zh": zh.strip(), "shared": shared.strip()}
    match = ZH_LABEL.search(text)
    if match:
        return {"mode": "label", "en": text[: match.start()].strip(), "zh": text[match.end():].strip(),
                "label": match.group(0).strip()}
    return {"mode": "single", "text": text}


def language_text(parts: dict[str, str], language: str) -> str:
    if parts["mode"] == "single":
        return parts["text"]
    return parts["en"] if language == "en" else parts["zh"]


def join_languages(parts: dict[str, str], language: str, value: str) -> str:
    if parts["mode"] == "single":
        return value
    en = value.strip() if language == "en" else parts["en"]
    zh = parts["zh"] if language == "en" else value.strip()
    if parts["mode"] == "marked":
        lines = [MARK_EN, en, "", MARK_ZH, zh]
        if parts.get("shared"):
            lines += ["", MARK_SHARED, parts["shared"]]
        return "\n".join(lines) + "\n"
    return "\n".join([en, "", parts["label"], "", zh]) + "\n"


# ---------- checks ----------
def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER.findall(PLACEHOLDER.sub(" ", IDENTIFIER.sub(" ", text)))}


def _assets(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if ASSET.match(line)]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[。！？])|(?<=[.!?])\s+(?=[A-Z\[(])|\n+", text) if s and s.strip()]


def check_revision(original: str, revised: str, language: str) -> list[str]:
    problems: list[str] = []
    if not revised.strip():
        return ["改写结果为空"]
    added = sorted(_numbers(revised) - _numbers(original))
    if added:
        problems.append("新增了原文没有的数字：" + "、".join(added[:6]))
    dropped = sorted(_numbers(original) - _numbers(revised))
    if dropped:
        problems.append("删掉了原文的数字：" + "、".join(dropped[:6]))
    new_ids = set(IDENTIFIER.findall(revised)) - set(IDENTIFIER.findall(original))
    if new_ids:
        problems.append("新增了样本或项目编号：" + "、".join(sorted(new_ids)[:4]))
    lost_ids = set(IDENTIFIER.findall(original)) - set(IDENTIFIER.findall(revised))
    if lost_ids:
        problems.append("删掉了样本或项目编号：" + "、".join(sorted(lost_ids)[:4]))
    new_keys = set(citation_keys(revised)) - set(citation_keys(original))
    if new_keys:
        problems.append("新增了引用：" + "、".join(sorted(new_keys)))
    lost_keys = set(citation_keys(original)) - set(citation_keys(revised))
    if lost_keys:
        problems.append("删掉了引用：" + "、".join(sorted(lost_keys)))
    lost_assets = [a for a in _assets(original) if a not in _assets(revised)]
    if lost_assets:
        problems.append(f"改动或删掉了 {len(lost_assets)} 行图表（图片行和图注须原样保留）")
    if len(PLACEHOLDER.findall(revised)) < len(PLACEHOLDER.findall(original)):
        problems.append("删掉了「作者填写 / 待补 Skill」占位，请在编辑器中直接补写")
    if "<<<" in revised or ">>>" in revised:
        problems.append("结果里混进了 <<< >>> 分隔符")
    if len(revised) > 2 * len(original) + 300:
        problems.append("改写后篇幅比原文长出一倍以上")
    cjk, latin = len(CJK.findall(revised)), len(LATIN.findall(revised))
    if language == "zh" and cjk < 0.3 * max(cjk + latin, 1):
        problems.append("要求改中文版，但结果不是中文")
    if language == "en" and cjk > 0.05 * max(cjk + latin, 1):
        problems.append("要求改英文版，但结果含有大量中文")
    return problems


def changed_sentences(original: str, revised: str) -> int:
    a, b = _sentences(original), _sentences(revised)
    ops = difflib.SequenceMatcher(None, a, b).get_opcodes()
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in ops if tag != "equal")


# ---------- model ----------
def _prompt(text: str, request: str, language: str, title: str, history: list[dict[str, str]],
            problems: list[str]) -> str:
    lang_name = "Chinese (简体中文)" if language == "zh" else "English"
    talk = "\n".join(f"{'Author' if m.get('role') == 'user' else 'Assistant'}: {m.get('content', '')}"
                     for m in history[-6:])
    parts = [
        f"Section: {title}",
        f"Language of the text: {lang_name}. Write the revised text in {lang_name}.",
        "",
        "Current text of the section:",
        "<<<",
        text,
        ">>>",
    ]
    if talk:
        parts += ["", "Earlier conversation about this section:", talk]
    parts += [
        "",
        f"Author's request: {request}",
        "",
        "Rules:",
        "- Make a real, visible change to the part the request is about (when asked to be more concise, "
        "cut redundant words or merge clauses so it becomes clearly shorter). Copy every other sentence unchanged.",
        "- Keep every number, unit, sample or run identifier (e.g. ERR3450229), citation marker such as "
        "[@Key], and every figure line (lines starting with ![ or **Figure) exactly as written.",
        "- Do not add any number, finding, method, citation or claim that is not already in the text.",
        "- Keep markers such as [AUTHOR_INPUT_NEEDED: ...] as they are.",
        "- revised: the complete revised text of the section (all paragraphs, not only the changed part).",
        "- reply: one or two sentences in Chinese telling the author what you changed. If the request cannot "
        "be done without adding facts, say so and return the text unchanged.",
    ]
    if problems:
        parts += ["", "Your previous attempt was rejected for these reasons; fix them:",
                  *[f"- {p}" for p in problems]]
    return "\n".join(parts)


def _ask(model: Any, prompt: str) -> dict[str, str]:
    if hasattr(model, "generate_json"):
        data = model.generate_json(prompt, SYSTEM, SCHEMA)
    else:
        raw = model.generate(prompt + "\n\nAnswer with JSON: {\"reply\": ..., \"revised\": ...}", system=SYSTEM)
        raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    return {"reply": str(data.get("reply") or "").strip(), "revised": strip_fences(str(data.get("revised") or ""))}


FENCE_LINE = re.compile(r"^\s*(?:<<<|>>>|```[a-z]*)\s*$", re.M)


def strip_fences(text: str) -> str:
    """Models sometimes echo the <<< >>> delimiters or wrap the text in a code fence."""

    return FENCE_LINE.sub("", text).strip()


def revise_chapter(markdown: str, request: str, language: str, model: Any, title: str = "",
                   history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Return {status, markdown, reply, changed, problems}. status: revised | unchanged | rejected."""

    language = "en" if language == "en" else "zh"
    parts = split_languages(markdown)
    original = language_text(parts, language)
    problems: list[str] = []
    answer: dict[str, str] = {"reply": "", "revised": ""}
    unchanged = False
    for _attempt in range(2):
        answer = _ask(model, _prompt(original, request, language, title, history or [], problems))
        revised = answer["revised"].strip()
        problems = check_revision(original, revised, language)
        unchanged = not problems and changed_sentences(original, revised) == 0
        if unchanged:
            # The model often says "done" and hands back the same text; ask once more, explicitly.
            problems = ["revised 与原文完全相同。请真正按要求改写相关句子（例如要求更简洁时，删去冗余词语、合并或缩短句子），"
                        "其他句子保持不变。"]
            continue
        if not problems:
            break
    if unchanged:
        return {"status": "unchanged", "markdown": markdown, "reply": answer["reply"], "changed": 0,
                "problems": []}
    if problems:
        return {"status": "rejected", "markdown": markdown, "reply": answer["reply"], "changed": 0,
                "problems": problems}
    revised = answer["revised"].strip()
    changed = changed_sentences(original, revised)
    if changed == 0:
        return {"status": "unchanged", "markdown": markdown, "reply": answer["reply"], "changed": 0,
                "problems": []}
    return {"status": "revised", "markdown": join_languages(parts, language, revised), "reply": answer["reply"],
            "changed": changed, "problems": [], "bilingual": parts["mode"] != "single"}


def chat_note(result: dict[str, Any], language: str) -> str:
    other = "英文版" if language == "zh" else "中文版"
    if result["status"] == "revised":
        note = (result.get("reply") or "已按要求修改。").strip()
        note += f"\n\n（改动 {result['changed']} 句；数字、编号、引用和图已核对未变"
        note += f"；{other}未改动）" if result.get("bilingual") else "）"
        return note
    if result["status"] == "unchanged":
        reply = (result.get("reply") or "").strip()
        return (reply + "\n\n" if reply else "") + "（模型没有给出实际改动，原文保留。可以把要求写得更具体，例如指明哪一段、哪一句。）"
    return ("没有应用这次修改，原文保留。模型的改写没有通过核对：\n"
            + "\n".join(f"- {p}" for p in result.get("problems", []))
            + "\n可以换个说法再试，或直接在编辑器里修改。")


@register_skill
class ChapterChatReviserSkill(BaseSkill):
    spec = SkillSpec(name="chapter-chat-reviser", version="1.0.0", category=SkillCategory.WRITING,
                     description="与模型对话修改单个章节：只改作者当前查看的语言版本；不得新增或删掉数字、编号、引用和图，"
                                 "核对不通过则重试一次，仍不通过就保留原文并说明原因。",
                     tags=("writing", "revision", "chat", "llm"),
                     input_keys=("markdown", "request", "language", "model"),
                     output_keys=("status", "markdown", "reply", "changed", "problems"))

    def run(self, skill_input: SkillInput):
        p = skill_input.require("markdown", "request", "model")
        data = revise_chapter(p["markdown"], p["request"], skill_input.get("language") or "zh", p["model"],
                              title=skill_input.get("title") or "", history=skill_input.get("history") or [])
        return self.success(data=data, summary=f"{data['status']} · 改动 {data['changed']} 句")
