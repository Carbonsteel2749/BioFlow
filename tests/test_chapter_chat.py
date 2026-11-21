"""chapter-chat-reviser: one language half, fact guards, honest unchanged/rejected outcomes."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bioflow_skills.skills.writing.chapter_chat import (  # noqa: E402
    chat_note, check_revision, join_languages, revise_chapter, split_languages,
)

EN = "Raw reads ranged from 46,952,652 to 90,100,086 [@Wood2019]. The study is limited to descriptive analysis."
ZH = "原始读数为46,952,652至90,100,086条[@Wood2019]。本研究仅限于描述性分析。"
DOC = f"{EN}\n\n中文\n\n{ZH}\n"


class Model:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def generate_json(self, prompt, system, schema):
        self.prompts.append(prompt)
        revised = self.answers.pop(0)
        return {"reply": "已修改。", "revised": revised}


def test_split_and_join_round_trip():
    parts = split_languages(DOC)
    assert parts["mode"] == "label" and parts["en"] == EN and parts["zh"] == ZH
    assert join_languages(parts, "zh", ZH) == DOC
    marked = "<!-- lang:en -->\nE\n\n<!-- lang:zh -->\n中\n\n<!-- lang:shared -->\n![f](figures/a.png)\n"
    again = split_languages(join_languages(split_languages(marked), "en", "E2"))
    assert again == {"mode": "marked", "en": "E2", "zh": "中", "shared": "![f](figures/a.png)"}


def test_revises_only_the_requested_language():
    new_zh = "原始读数为46,952,652至90,100,086条[@Wood2019]。本研究仅做描述性分析。"
    model = Model(new_zh)
    result = revise_chapter(DOC, "最后一句更简洁", "zh", model)
    assert result["status"] == "revised" and result["changed"] == 1
    parts = split_languages(result["markdown"])
    assert parts["en"] == EN and parts["zh"] == new_zh
    assert EN not in model.prompts[0]  # the model never sees the other language
    assert "英文版未改动" in chat_note(result, "zh")


def test_new_number_is_rejected_after_one_retry():
    bad = ZH + "受试者平均年龄为45岁。"
    model = Model(bad, bad)
    result = revise_chapter(DOC, "加上平均年龄45岁", "zh", model)
    assert result["status"] == "rejected" and result["markdown"] == DOC
    assert any("45" in p for p in result["problems"])
    assert len(model.prompts) == 2 and "rejected" in model.prompts[1]


def test_identical_text_is_retried_then_reported_unchanged():
    model = Model(ZH, ZH)
    result = revise_chapter(DOC, "更简洁", "zh", model)
    assert result["status"] == "unchanged" and result["markdown"] == DOC
    assert len(model.prompts) == 2
    assert "没有给出实际改动" in chat_note(result, "zh")


def test_small_real_edit_counts_and_fences_are_stripped():
    model = Model("<<<\n原始读数为46,952,652至90,100,086条[@Wood2019]。本研究仅做描述性分析。\n>>>")
    result = revise_chapter(DOC, "更简洁", "zh", model)
    assert result["status"] == "revised" and "<<<" not in result["markdown"]


def test_guards_cover_citations_ids_figures_and_language():
    original = "Sample ERR3450229 had 134 pairs [@Wood2019].\n\n![f](figures/a.png)\n\n**Figure 1.** Taxa."
    assert any("引用" in p for p in check_revision(original, original.replace(" [@Wood2019]", ""), "en"))
    assert any("编号" in p for p in check_revision(original, original.replace("ERR3450229", "the sample"), "en"))
    assert any("图表" in p for p in check_revision(original, original.replace("**Figure 1.** Taxa.", ""), "en"))
    assert any("不是中文" in p for p in check_revision(ZH, EN, "zh"))
    assert check_revision(original, original, "en") == []
