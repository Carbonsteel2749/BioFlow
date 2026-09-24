"""Tests for Word/PDF export helpers (LibreOffice-backed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from frontend.export_formats import export_word_and_pdf, markdown_to_html


SAMPLE = """# Demo Paper

## English

Results show **128** genes and cite lit_001.

## 中文

结果显示 128 个基因。

- item A
- item B
"""


def test_markdown_to_html_contains_bilingual_headings():
    html = markdown_to_html(SAMPLE, title="Demo Paper")
    assert "English" in html
    assert "中文" in html
    assert "<strong>128</strong>" in html


def test_export_word_and_pdf(tmp_path: Path):
    result = export_word_and_pdf(SAMPLE, tmp_path, basename="manuscript_final", title="Demo")
    assert Path(result["markdown"]).is_file()
    assert Path(result["html"]).is_file()
    if not result["docx"] and not result["pdf"]:
        pytest.skip(
            f"LibreOffice unavailable or failed: "
            f"{result.get('docx_error') or result.get('pdf_error')}"
        )
    if result["docx"]:
        assert Path(result["docx"]).is_file()
    if result["pdf"]:
        assert Path(result["pdf"]).is_file()
