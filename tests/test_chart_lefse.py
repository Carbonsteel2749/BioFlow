import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pytest

from backend.bioflow_platform.charts.registry import get
from backend.bioflow_platform.charts.lefse import draw


def example_rows():
    return [
        {"taxon": "Feature A", "lda_score": 3.8, "group": "Control"},
        {"taxon": "Feature B", "lda_score": 3.1, "group": "Treatment"},
        {"taxon": "Feature C", "lda_score": 2.6, "group": "Control"},
        {"taxon": "Feature D", "lda_score": 2.2, "group": "Treatment"},
    ]


def test_lefse_is_registered():
    chart = get("lefse")
    assert chart is not None
    assert chart["id"] == "lefse"
    assert "lda_score" in chart["columns"]


def test_lefse_draws_taxa_and_group_legend():
    fig, ax = plt.subplots()
    try:
        draw(example_rows(), fig, ax, {})

        assert ax.get_xlabel() == "LDA score"
        assert ax.get_ylabel() == "Taxon"
        assert len(ax.get_yticklabels()) == 4

        groups = {item[0] for item in ax._bioflow_series}
        assert groups == {"group:Control", "group:Treatment"}
    finally:
        plt.close(fig)


def test_lefse_rejects_missing_required_columns():
    fig, ax = plt.subplots()
    try:
        with pytest.raises(ValueError):
            draw(
                [{"taxon": "Feature A", "lda_score": 3.8}],
                fig, ax, {}
            )
    finally:
        plt.close(fig)


def test_lefse_rejects_empty_group():
    fig, ax = plt.subplots()
    try:
        with pytest.raises(ValueError):
            draw(
                [{"taxon": "Feature A", "lda_score": 3.8, "group": ""}],
                fig, ax, {}
            )
    finally:
        plt.close(fig)


def test_lefse_rejects_more_than_100_taxa():
    rows = [
        {"taxon": f"Feature {i}", "lda_score": i / 10, "group": "Control"}
        for i in range(101)
    ]
    fig, ax = plt.subplots()
    try:
        with pytest.raises(ValueError):
            draw(rows, fig, ax, {})
    finally:
        plt.close(fig)
