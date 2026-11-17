import pytest


def test_volcano_thresholds_and_fdr_label():
    pytest.importorskip('matplotlib')
    import matplotlib.pyplot as plt
    from backend.bioflow_platform.charts.volcano import draw

    rows = [
        {'gene': 'UpA', 'log2fc': '2', 'pvalue': '0.001'},
        {'gene': 'DownB', 'log2fc': '-2', 'pvalue': '0.01'},
        {'gene': 'Weak', 'log2fc': '0.2', 'pvalue': '0.5'},
        {'gene': 'NotSig', 'log2fc': '1.2', 'pvalue': '0.2'},
    ]

    fig, ax = plt.subplots()
    try:
        draw(rows, fig, ax, {
            'volcano_fc_threshold': 1,
            'volcano_p_threshold': 0.05,
            'volcano_p_label': 'FDR',
            'volcano_label_top_n': 2,
            'volcano_show_fc_lines': True,
            'volcano_show_p_line': True,
        })

        assert ax.get_ylabel() == '−log10(FDR)'
        assert len(ax.lines) == 3
        assert {t.get_text() for t in ax.texts} == {'UpA', 'DownB'}
    finally:
        plt.close(fig)


def test_heatmap_zscore_scale_and_group_annotation():
    pytest.importorskip('matplotlib')
    import matplotlib.pyplot as plt
    from backend.bioflow_platform.charts.heatmap import draw

    rows = [
        {'sample': 'S1', 'group': 'A', 'G1': '-1', 'G2': '0', 'G3': '1'},
        {'sample': 'S2', 'group': 'A', 'G1': '-0.5', 'G2': '0.2', 'G3': '1.2'},
        {'sample': 'S3', 'group': 'B', 'G1': '1', 'G2': '0', 'G3': '-1'},
    ]

    fig, ax = plt.subplots()
    try:
        draw(rows, fig, ax, {
            'heatmap_palette': 'RdBu_r',
            'heatmap_color_limit': 2.5,
            'heatmap_center_zero': True,
            'heatmap_cluster_rows': False,
            'heatmap_cluster_columns': False,
            'heatmap_show_group': True,
        })

        assert ax.images[0].get_clim() == (-2.5, 2.5)
        assert len(ax.patches) == 3
        assert {item[0] for item in ax._bioflow_series} == {
            'group:A', 'group:B'
        }
    finally:
        plt.close(fig)


def test_plot_controls_are_validated_by_contract():
    from pydantic import ValidationError
    from backend.bioflow_platform.chart_contracts import DisplayParameters

    params = DisplayParameters(
        volcano_fc_threshold=1.5,
        volcano_p_threshold=0.01,
        volcano_p_label='FDR',
        volcano_label_top_n=8,
        heatmap_palette='RdBu_r',
    )
    assert params.volcano_fc_threshold == 1.5
    assert params.volcano_p_label == 'FDR'

    with pytest.raises(ValidationError):
        DisplayParameters(volcano_fc_threshold=0.01)
