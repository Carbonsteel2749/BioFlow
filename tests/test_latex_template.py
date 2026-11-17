import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "article_writing"))

from frontend.latex_preview import compile_latex
from frontend.latex_template import TemplateError, fill_template_text, install_template, render_for_project
from frontend.session_store import SectionState, WritingSession


def _session(tmp_path: Path) -> WritingSession:
    return WritingSession(
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
                "Richness was 12.\n\n中文\n\n丰富度在患者组更低。\n",
            ),
        },
        section_order=["abstract", "results"],
        output_dir=str(tmp_path),
    )


def test_slots_keep_one_language(tmp_path, monkeypatch):
    monkeypatch.setattr("frontend.latex_template.template_root", lambda: tmp_path / "templates")
    template = (
        "\\documentclass[UTF8,fontset=fandol]{ctexart}\n"
        "\\begin{document}\n"
        "\\title{<<TITLE>>}\n\\maketitle\n"
        "<<QUESTION>>\n"
        "\\begin{abstract}\n<<ABSTRACT>>\n\\end{abstract}\n"
        "<<RESULTS>>\n"
        "\\end{document}\n"
    )
    meta = install_template("proj123", "journal.tex", template.encode())
    assert meta["mode"] == "slots"
    session = _session(tmp_path)
    chinese, _support, stamp = render_for_project(session, "zh", "proj123")
    english, _support, _stamp = render_for_project(session, "en", "proj123")
    assert stamp
    assert "患者的多样性低于对照组" in chinese
    assert "Alpha diversity was lower" not in chinese
    assert "只属于中文版" in chinese
    assert "Alpha diversity was lower in patients." in english
    assert "患者的多样性低于对照组" not in english
    assert "只属于中文版" not in english
    assert "丰富度在患者组更低" in chinese
    pdf, err = compile_latex(session, chinese, _support)
    assert pdf and pdf.is_file() and not err


def test_preamble_replaces_sample_body(tmp_path, monkeypatch):
    monkeypatch.setattr("frontend.latex_template.template_root", lambda: tmp_path / "templates")
    template = (
        "\\documentclass[UTF8,fontset=fandol]{ctexart}\n"
        "\\newcommand{\\journalname}{Demo Journal}\n"
        "\\begin{document}\n"
        "This old sample paragraph should disappear.\n"
        "\\end{document}\n"
    )
    install_template("proj123", "plain.tex", template.encode())
    filled, _support, _stamp = render_for_project(_session(tmp_path), "zh", "proj123")
    assert r"\newcommand{\journalname}{Demo Journal}" in filled
    assert "This old sample paragraph should disappear." not in filled
    assert r"\section{摘要}" in filled


def test_unsafe_template_and_zip_path_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr("frontend.latex_template.template_root", lambda: tmp_path / "templates")
    with pytest.raises(TemplateError):
        install_template("proj123", "bad.tex", b"\\documentclass{article}\n\\begin{document}\n\\write18{rm -rf /}\n\\end{document}\n")
    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../evil.tex", "\\documentclass{article}\n\\begin{document}\n\\end{document}\n")
    with pytest.raises(TemplateError):
        install_template("proj123", "pack.zip", payload.getvalue())
    kept = fill_template_text("See <<NOT_A_SLOT>> later.\n<<ABSTRACT>>\n", _session(tmp_path), "en")
    assert "<<NOT_A_SLOT>>" in kept
    assert "Alpha diversity was lower in patients." in kept
