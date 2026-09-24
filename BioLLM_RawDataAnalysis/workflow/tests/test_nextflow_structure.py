import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_nextflow_modules_have_expected_processes():
    expected = {
        "validate.nf": "VALIDATE_MANIFEST",
        "fastqc.nf": "FASTQC_RAW",
        "fastp.nf": "FASTP",
        "host_depletion.nf": "HOST_DEPLETION",
        "taxonomy.nf": "TAXONOMY",
        "functional_annotation.nf": "FUNCTIONAL_ANNOTATION",
        "assembly.nf": "ASSEMBLY",
        "binning.nf": "BINNING",
        "bin_refinement.nf": "BIN_REFINEMENT",
        "bin_quantification.nf": "BIN_QUANTIFICATION",
        "bin_reassembly.nf": "BIN_REASSEMBLY",
        "bin_annotation.nf": "BIN_ANNOTATION",
        "report.nf": "REPORT",
    }
    for filename, process in expected.items():
        content = (ROOT / "modules" / filename).read_text(encoding="utf-8")
        assert f"process {process}" in content
        assert "/bin/" in content
        assert "TODO" not in content
        assert "TBD" not in content


def test_main_has_core_and_optional_mag_branches():
    content = (ROOT / "main.nf").read_text(encoding="utf-8")
    for process in (
        "VALIDATE_MANIFEST", "FASTQC_RAW", "FASTP", "HOST_DEPLETION",
        "TAXONOMY", "FUNCTIONAL_ANNOTATION", "REPORT",
    ):
        assert process in content
    assert "if (params.enable_mags.toString().toBoolean())" in content
    assert "if (params.enable_reassembly.toString().toBoolean())" in content
    assert ".flatMap { outputs -> outputs.drop(1) }" in content
    for process in (
        "ASSEMBLY", "BINNING", "BIN_REFINEMENT", "BIN_QUANTIFICATION", "BIN_ANNOTATION",
    ):
        assert process in content


def test_taxonomy_publish_directory_is_resolved_per_sample():
    content = (ROOT / "modules" / "taxonomy.nf").read_text(encoding="utf-8")

    assert "publishDir path: {" in content
    assert '"${params.outdir}/taxonomy/${sample_id}"' in content


def test_functional_annotation_publish_directory_is_resolved_per_sample():
    content = (ROOT / "modules" / "functional_annotation.nf").read_text(encoding="utf-8")

    assert "publishDir path: {" in content
    assert '"${params.outdir}/functional_annotation/${sample_id}"' in content
    assert "--database-manifest ${database_manifest}" in content
    assert 'tuple val(sample_id), path("read_pathcoverage.tsv"), emit: pathway_coverage' in content
    assert 'tuple val(sample_id), path("read_ko.tsv"), emit: ko' in content
    assert 'tuple val(sample_id), path("read_ec.tsv"), emit: ec' in content
    assert 'path("humann_raw_ko.tsv")' in content
    assert 'path("humann_raw_ec.tsv")' in content


def test_report_publish_directory_does_not_duplicate_report_folder():
    content = (ROOT / "modules" / "report.nf").read_text(encoding="utf-8")

    assert 'publishDir "${params.outdir}", mode:' in content
    assert 'path "report/multiqc_report.html"' in content
    assert 'path "report/multiqc_report_data"' in content
    assert 'path report_files, stageAs: "inputs/*/*"' in content


def test_report_stages_database_manifest_as_an_explicit_control_file():
    module = (ROOT / "modules" / "report.nf").read_text(encoding="utf-8")
    script = (ROOT / "bin" / "core" / "report.sh").read_text(encoding="utf-8")

    assert 'path database_manifest, name: "report-database.resolved.json"' in module
    assert "val nextflow_work_root" in module
    assert '--nextflow-work-root "${nextflow_work_root}"' in module
    assert '--exclude-artifact "$database_manifest"' in script
    assert '--nextflow-work-root "$nextflow_work_root"' in script


def test_main_routes_optional_functional_and_mag_tables_to_report():
    content = (ROOT / "main.nf").read_text(encoding="utf-8")

    assert ".mix(FUNCTIONAL_ANNOTATION.out.reports)" in content
    assert ".mix(BIN_QUANTIFICATION.out.abundance)" in content
    assert ".mix(BIN_ANNOTATION.out.mag_taxonomy.flatMap" in content
    assert ".mix(BIN_ANNOTATION.out.mag_functions.flatMap" in content
    functional = (ROOT / "modules" / "functional_annotation.nf").read_text(
        encoding="utf-8"
    )
    reports_contract = next(
        line for line in functional.splitlines() if "emit: reports" in line
    )
    for filename in ("read_pathcoverage.tsv", "read_ko.tsv", "read_ec.tsv"):
        assert filename in reports_contract
    assert "workflow.workDir.toString()" in content


def test_resume_launcher_keeps_stable_work_directory():
    content = (ROOT / "run_pipeline.sh").read_text(encoding="utf-8")
    assert "-resume" in content
    assert '"$outdir/work"' in content
    assert 'database_manifest="$outdir/.pipeline/database.resolved.json"' in content
    assert "--enable_mags" in content


def test_parameter_schema_exposes_preprocessing_controls():
    schema = json.loads((ROOT / "assets" / "params.schema.json").read_text(encoding="utf-8"))
    properties = schema["properties"]
    expected = {
        "threads",
        "fastp_qualified_quality_phred",
        "fastp_unqualified_percent_limit",
        "fastp_n_base_limit",
        "fastp_length_required",
        "fastp_cut_front",
        "fastp_cut_tail",
        "fastp_cut_window_size",
        "fastp_cut_mean_quality",
        "fastp_trim_poly_g",
        "fastp_correction",
        "fastp_detect_adapter_for_pe",
        "host_index",
        "host_filter_mode",
        "host_min_retained_pairs",
        "host_max_removed_pct",
        "host_bowtie2_preset",
        "database_registry",
        "database_profile",
        "database_manifest",
        "read_length",
    }

    assert expected <= properties.keys()
    assert properties["host_filter_mode"]["enum"] == [
        "strict_both_unmapped",
        "concordant_unmapped",
    ]
    assert properties["read_length"]["enum"] == [50, 75, 100, 150, 200, 250, 300]


def test_backend_launcher_exposes_functional_database_defaults():
    content = (ROOT.parent / "scripts" / "run_backend.sh").read_text(encoding="utf-8")

    assert "BIOLLM_HUMANN_NUCLEOTIDE_DB" in content
    assert "/home/xh/databases/humann/chocophlan" in content
    assert "BIOLLM_HUMANN_PROTEIN_DB" in content
    assert "/home/xh/databases/humann/uniref" in content
    assert "BIOLLM_METAPHLAN_DB" in content
    assert "/home/xh/databases/metaphlan/mpa_vJun23_CHOCOPhlAnSGB_202403/index" in content


def test_database_manifest_is_a_path_input_for_annotation_processes():
    for filename in ("taxonomy.nf", "functional_annotation.nf", "bin_annotation.nf", "bin_quantification.nf"):
        content = (ROOT / "modules" / filename).read_text(encoding="utf-8")
        assert "path database_manifest" in content


def test_annotation_modules_expose_typed_dynamic_node_outputs():
    taxonomy = (ROOT / "modules" / "taxonomy.nf").read_text(encoding="utf-8")
    functional = (ROOT / "modules" / "functional_annotation.nf").read_text(encoding="utf-8")
    mag = (ROOT / "modules" / "bin_annotation.nf").read_text(encoding="utf-8")

    for emit in ("species_abundance", "bracken_species", "kraken_artifacts", "validation", "provenance"):
        assert f"emit: {emit}" in taxonomy
    for emit in ("gene_families", "ko", "ec", "pathway_abundance", "pathway_coverage", "raw_humann", "validation", "provenance"):
        assert f"emit: {emit}" in functional
    for emit in ("raw_taxonomy", "raw_functions", "mag_taxonomy", "mag_functions", "provenance"):
        assert f"emit: {emit}" in mag
    assert "tuple val(cohort_id), path(bins)" in mag
    assert "tuple val(sample_id)" not in mag


def test_fixed_workflow_adapts_coassembly_to_mag_cohort_node_and_keeps_mag_optional():
    content = (ROOT / "main.nf").read_text(encoding="utf-8")
    assert "if (params.enable_mags.toString().toBoolean())" in content
    assert "tuple('coassembly', bins)" in content
    assert "BIN_ANNOTATION(mag_annotation_input, database_manifest_ch)" in content
    assert "BIN_ANNOTATION.out.mag_taxonomy" in content


def test_manifest_path_is_a_cache_identity_for_all_annotation_nodes():
    for filename in ("taxonomy.nf", "functional_annotation.nf", "bin_annotation.nf"):
        content = (ROOT / "modules" / filename).read_text(encoding="utf-8")
        assert "path database_manifest" in content

def test_main_requires_resolved_database_manifest():
    content = (ROOT / "main.nf").read_text(encoding="utf-8")
    assert "--database_manifest is required" in content
    assert "Channel.value(file(params.database_manifest" in content
