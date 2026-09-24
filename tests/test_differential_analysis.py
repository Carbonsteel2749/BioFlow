import tempfile
from pathlib import Path

import pandas as pd

from bioflow.core.context import RunContext
from bioflow.core.models import (
    ArtifactManifest,
    DatasetSpec,
    DatasetType,
    ModuleInput,
    ModuleOutput,
    ModuleStatus,
)
from bioflow.modules.analysis.differential_analysis import (
    DifferentialAnalysisModule,
    DifferentialAnalyzer,
    GroupResolver,
)


def _write_normalized_matrix(path: Path) -> None:
    frame = pd.DataFrame(
        {
            "ctrl_1": [1.0, 7.0, 3.0],
            "ctrl_2": [1.1, 7.2, 3.2],
            "treat_1": [8.0, 2.0, 3.1],
            "treat_2": [8.2, 2.1, 3.0],
        },
        index=["gene_up", "gene_down", "gene_flat"],
    )
    frame.index.name = "gene_id"
    frame.to_csv(path)


def test_group_resolver_from_payload_groups():
    groups = GroupResolver().resolve(
        sample_ids=["s1", "s2", "s3", "s4"],
        payload={"groups": {"control": ["s1", "s2"], "treated": ["s3", "s4"]}},
    )

    assert groups == {
        "s1": "control",
        "s2": "control",
        "s3": "treated",
        "s4": "treated",
    }


def test_group_resolver_auto_detect_by_prefix():
    groups = GroupResolver().resolve(
        sample_ids=["ctrl_1", "ctrl_2", "treat_1", "treat_2"],
        payload={},
    )

    assert groups == {
        "ctrl_1": "ctrl",
        "ctrl_2": "ctrl",
        "treat_1": "treat",
        "treat_2": "treat",
    }


def test_differential_analyzer_marks_significant_genes():
    matrix = pd.DataFrame(
        {
            "ctrl_1": [1.0, 4.0],
            "ctrl_2": [1.0, 4.1],
            "treat_1": [8.0, 4.0],
            "treat_2": [8.0, 4.1],
        },
        index=["gene_up", "gene_flat"],
    )
    groups = {
        "ctrl_1": "ctrl",
        "ctrl_2": "ctrl",
        "treat_1": "treat",
        "treat_2": "treat",
    }

    result = DifferentialAnalyzer().run_comparison(
        matrix,
        groups,
        ("ctrl", "treat"),
        {
            "log2fc_threshold": 1.0,
            "padj_threshold": 0.05,
            "min_samples_per_group": 2,
            "method": "ttest",
            "pseudocount": 1e-6,
        },
    )
    frame = DifferentialAnalyzer.to_dataframe(result)

    gene_up = frame.loc[frame["gene_id"] == "gene_up"].iloc[0]
    gene_flat = frame.loc[frame["gene_id"] == "gene_flat"].iloc[0]
    assert gene_up["log2FoldChange"] > 2.0
    assert bool(gene_up["significant"]) is True
    assert bool(gene_flat["significant"]) is False


def test_differential_analysis_module_writes_deg_csv():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        norm_path = tmp_path / "normalized_matrix.csv"
        _write_normalized_matrix(norm_path)

        dataset = DatasetSpec(
            dataset_type=DatasetType.expression_matrix,
            primary_path=str(norm_path),
            format="csv",
            confidence=0.95,
            metadata={"separator": ","},
        )
        upstream = ModuleOutput(
            module="data_processing",
            status=ModuleStatus.succeeded,
            summary="ok",
            artifacts=ArtifactManifest(root=str(tmp_path)),
            dataset=dataset,
            metrics={"norm_matrix_path": str(norm_path)},
        )
        context = RunContext(run_id="test-run", workspace=tmp_path)
        module_input = ModuleInput(
            run_id="test-run",
            module="differential_analysis",
            payload={"method": "ttest"},
            deps={"data_processing": upstream},
        )

        output = DifferentialAnalysisModule().run(context, module_input)

        assert output.status == ModuleStatus.succeeded
        assert output.module == "differential_analysis"
        assert output.metrics["deg_comparison_count"] == 1
        assert output.tables
        deg_path = Path(output.tables[0].path)
        assert deg_path.exists()
        deg_frame = pd.read_csv(deg_path)
        assert list(deg_frame.columns) == [
            "gene_id",
            "baseMean",
            "mean_A",
            "mean_B",
            "log2FoldChange",
            "pvalue",
            "padj",
            "pvalue_wilcoxon",
            "padj_wilcoxon",
            "significant",
        ]
        assert {"gene_up", "gene_down", "gene_flat"} == set(deg_frame["gene_id"])