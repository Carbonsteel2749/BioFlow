from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_main_wires_all_visualization_processes_to_pipeline_outputs():
    text = (ROOT / "main.nf").read_text(encoding="utf-8")
    for process in ("TAXONOMY_PLOTS", "FUNCTIONAL_PLOTS", "MAG_PLOTS"):
        assert f"include {{ {process} }}" in text
        assert f"{process}(" in text

    assert "TAXONOMY.out.species_abundance" in text
    assert "FUNCTIONAL_ANNOTATION.out.gene_families" in text
    assert "BIN_ANNOTATION.out.mag_taxonomy" in text
    assert "BIN_QUANTIFICATION.out.abundance" in text
