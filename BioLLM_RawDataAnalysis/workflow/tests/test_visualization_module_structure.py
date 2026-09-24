from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_visualization_modules_have_explicit_standard_tsv_contracts():
    expected = {
        "taxonomy_plots.nf": ("TAXONOMY_PLOTS", ["path species_abundance", "emit: plots", "emit: provenance"]),
        "functional_plots.nf": ("FUNCTIONAL_PLOTS", ["path gene_families", "path ko", "path ec", "path pathway_abundance", "path pathway_coverage", "emit: plots", "emit: provenance"]),
        "mag_plots.nf": ("MAG_PLOTS", ["tuple val(cohort_id), path(mag_annotation)", "path sample_mag_abundance", "emit: plots", "emit: provenance"]),
    }
    for filename, (process, fragments) in expected.items():
        text = (ROOT / "modules" / filename).read_text(encoding="utf-8")
        assert f"process {process}" in text
        for fragment in fragments:
            assert fragment in text

def test_mag_plot_module_is_cohort_scoped_not_sample_scoped():
    text = (ROOT / "modules" / "mag_plots.nf").read_text(encoding="utf-8")
    assert "tuple val(sample_id)" not in text
    assert "cohort_id" in text
