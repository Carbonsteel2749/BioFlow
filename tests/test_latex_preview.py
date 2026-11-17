import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "article_writing"))

from frontend.latex_preview import markdown_to_latex, session_to_latex
from frontend.session_store import SectionState, WritingSession


def test_markdown_figure_becomes_includegraphics(tmp_path):
    figure = tmp_path / "alpha.png"
    figure.write_bytes(b"png")
    body = markdown_to_latex(
        "患者香农指数为 3.8。\n\n![Alpha](figures/alpha.png)\n\n**Figure 1.** Alpha diversity\n",
        tmp_path,
    )
    assert r"\includegraphics[width=0.92\linewidth]{alpha.png}" in body
    assert r"\caption{Figure 1. Alpha diversity}" in body
    assert "3.8" in body


def test_session_latex_uses_ctex_and_section_title():
    session = WritingSession(
        session_id="abc",
        run_id="ui-abc",
        title="粪便微生物组_测试",
        research_question="早期检测",
        sections={
            "results": SectionState("results", "Results", "结果", "患者组香农指数下降 10%，丰富度也低于对照组。"),
        },
        section_order=["results"],
        output_dir="/tmp/does-not-need-figures",
    )
    tex = session_to_latex(session)
    assert tex.startswith(r"\documentclass[UTF8,fontset=fandol]{ctexart}")
    assert r"\section{结果}" in tex
    assert r"粪便微生物组\_测试" in tex
    assert r"10\%" in tex


def test_language_choice_keeps_one_language(tmp_path):
    figure = tmp_path / "pipeline" / "figures" / "alpha.png"
    figure.parent.mkdir(parents=True)
    figure.write_bytes(b"png")
    session = WritingSession(
        session_id="abc",
        run_id="ui-abc",
        title="粪便微生物组用于结直肠癌早期检测",
        research_question="这是一段只属于中文版的研究问题，英文版不应出现这段话。",
        sections={
            "abstract": SectionState(
                "abstract",
                "Abstract",
                "摘要",
                "Alpha diversity was lower in patients.\n\n中文\n\n患者的多样性低于对照组。\n",
            ),
            "results": SectionState(
                "results",
                "Results",
                "结果",
                "Richness was 12.\n\n中文\n\n丰富度在患者组更低。\n\n![Alpha](figures/alpha.png)\n\n**Figure 1.** Alpha diversity\n",
            ),
        },
        section_order=["abstract", "results"],
        output_dir=str(tmp_path),
    )
    chinese = session_to_latex(session, "zh")
    english = session_to_latex(session, "en")
    assert r"\section{摘要}" in chinese
    assert "患者的多样性低于对照组" in chinese
    assert "Alpha diversity was lower" not in chinese
    assert "只属于中文版" in chinese
    english_body = english.split(r"\section{Results}", 1)[-1]
    assert r"\section{Abstract}" in english
    assert r"\section{Results}" in english
    assert "Alpha diversity was lower in patients." in english
    assert "患者" not in english_body
    assert "只属于中文版" not in english
    assert r"\includegraphics[width=0.92\linewidth]{alpha.png}" in english
    assert r"\includegraphics[width=0.92\linewidth]{alpha.png}" in chinese
