import base64
import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "workflow" / "bin" / "core" / "package_results.py"


def load_module():
    spec = importlib.util.spec_from_file_location("package_results", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_result_archive_has_required_files_and_no_raw_reads(tmp_path, monkeypatch):
    work = tmp_path / "work"
    report = work / "report"
    state = tmp_path / "state"
    nextflow_work = tmp_path / "nextflow-work"
    tables = work / "tables"
    provenance = work / "provenance"
    archive = work / "deliverables" / "task-1.tar.gz"
    report.mkdir(parents=True)
    nextflow_work.mkdir()
    (report / "multiqc_report.html").write_text(
        f"<html>{nextflow_work}/cc/report</html>",
        encoding="utf-8",
    )
    multiqc_data = report / "multiqc_report_data"
    multiqc_data.mkdir()
    (multiqc_data / "multiqc_data.json").write_text(
        json.dumps({"source": str(nextflow_work / "aa" / "fastp.json")}) + "\n",
        encoding="utf-8",
    )
    (multiqc_data / "multiqc.log").write_text(
        f"scanned {nextflow_work}/aa token=unredacted\n",
        encoding="utf-8",
    )
    (multiqc_data / "multiqc.parquet").write_bytes(
        str(nextflow_work).encode(),
    )

    read1 = tmp_path / "S01_R1.fastq.gz"
    read2 = tmp_path / "S01_R2.fastq.gz"
    read1.write_bytes(b"read1")
    read2.write_bytes(b"read2")
    manifest = tmp_path / "samples.csv"
    manifest.write_text(
        f"sample_id,read1,read2\nS01,{read1},{read2}\n",
        encoding="utf-8",
    )
    database_manifest = tmp_path / "database.resolved.json"
    database_manifest.write_text(
        json.dumps(
            {
                "resolved_manifest_sha256": "db-sha",
                "databases": {
                    "taxonomy_reads": {
                        "kraken2": {
                            "database_name": "kraken",
                            "release": "v1",
                            "path": "/private/database",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (work / "S01.fastp.json").write_text(
        json.dumps(
            {
                "summary": {
                    "before_filtering": {"total_reads": 100},
                    "after_filtering": {"total_reads": 80},
                },
                "command": "/home/xh/private/bin/fastp --input staged.fastq.gz",
            }
        ),
        encoding="utf-8",
    )
    (work / "S01.host_depletion.metrics.json").write_text(
        json.dumps(
            {
                "sample_id": "S01",
                "input_pair_count": 40,
                "retained_pair_count": 35,
                "removed_pair_count": 5,
                "host_index_prefix": "/home/xh/databases/host/index",
            }
        ),
        encoding="utf-8",
    )
    (work / "species_abundance.tsv").write_text(
        "sample_id\ttaxonomy\tabundance\nS01\tE. coli\t0.9\n",
        encoding="utf-8",
    )
    (work / "read_genefamilies.tsv").write_text(
        "sample_id\tfunction_id\tabundance\nS01\tGF1\t1.2\n",
        encoding="utf-8",
    )
    (work / "read_pathabundance.tsv").write_text(
        "sample_id\tfunction_id\tabundance\nS01\tPWY1\t0.4\n",
        encoding="utf-8",
    )
    pathcoverage_source = nextflow_work / "aa" / "functional" / "read_pathcoverage.tsv"
    pathcoverage_source.parent.mkdir(parents=True)
    pathcoverage_source.write_text(
        "# Pathway\tS01_Coverage\nPWY1\t0.8\n",
        encoding="utf-8",
    )
    (work / "read_pathcoverage.tsv").symlink_to(pathcoverage_source)
    (work / "read_ko.tsv").write_text(
        "sample_id\tfunction_id\tabundance\nS01\tK00001\t0.3\n",
        encoding="utf-8",
    )
    (work / "read_ec.tsv").write_text(
        "sample_id\tfunction_id\tabundance\nS01\t1.1.1.1\t0.2\n",
        encoding="utf-8",
    )
    (work / "mag_annotation.tsv").write_text(
        "mag_id\ttaxonomy\tquality_flag\nMAG_001\tBacteria\tmedium\n",
        encoding="utf-8",
    )
    (work / "mag_function_annotation.tsv").write_text(
        "mag_id\tfunction_id\tfunction_name\nMAG_001\tK00001\tenzyme\n",
        encoding="utf-8",
    )
    (work / "sample_mag_abundance.tsv").write_text(
        "sample_id\tmag_id\tabundance\nS01\tMAG_001\t0.25\n",
        encoding="utf-8",
    )
    (work / "patient_R1.fastq.gz").write_bytes(b"must not be packaged")
    for group in ('taxonomy', 'functional', 'mag'):
        plots = nextflow_work / group
        plots.mkdir()
        png = plots / ('sample_mag_abundance_heatmap.png' if group == 'mag' else f'{group}_test.png')
        png.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        provenance_source = plots / f'{group}_plots.provenance.json'
        provenance_source.write_text(json.dumps({'input': str(nextflow_work / 'source.tsv'), 'outputs': [png.name]}))
        staged = work / 'inputs' / group
        staged.mkdir(parents=True)
        (staged / png.name).symlink_to(png)
        (staged / provenance_source.name).symlink_to(provenance_source)
    (work / 'unrelated.png').write_bytes(b'not an analysis figure')
    log_dir = state / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "S01.fastp.log").write_text(
        "S01 /home/xh/private token=topsecret 219.224.3.96\n"
        '{"password":"json-secret"}\n',
        encoding="utf-8",
    )
    parameters = base64.b64encode(
        json.dumps({"host_index": "/home/xh/databases/host"}).encode()
    ).decode()
    argv = [
        str(SCRIPT),
        "--task-id",
        "task-1",
        "--results-root",
        str(work),
        "--report-dir",
        str(report),
        "--tables-dir",
        str(tables),
        "--provenance-dir",
        str(provenance),
        "--archive",
        str(archive),
        "--state-root",
        str(state),
        "--nextflow-work-root",
        str(nextflow_work),
        "--input-manifest",
        str(manifest),
        "--database-manifest",
        str(database_manifest),
        "--parameters-base64",
        parameters,
        "--run-name",
        "test-run",
        "--project-root",
        str(PROJECT_ROOT),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    assert load_module().main() == 0
    assert archive.is_file()
    with tarfile.open(archive, "r:gz") as handle:
        names = set(handle.getnames())
        assert "README.txt" in names
        assert "reports/multiqc_report.html" in names
        assert "reports/summary.txt" in names
        assert "reports/summary.json" in names
        assert "reports/multiqc_report_data/multiqc_data.json" in names
        assert "reports/multiqc_report_data/multiqc.log" not in names
        assert "reports/multiqc_report_data/multiqc.parquet" not in names
        assert "tables/read_counts.tsv" in names
        for group in ('taxonomy', 'functional', 'mag'):
            filename = 'sample_mag_abundance_heatmap.png' if group == 'mag' else f'{group}_test.png'
            assert f'figures/{group}/{filename}' in names
            assert f'figures/{group}/{group}_plots.provenance.json' in names
        assert not any('unrelated.png' in name for name in names)
        assert "tables/species_abundance.tsv" in names
        assert "tables/gene_families.tsv" in names
        assert "tables/pathway_abundance.tsv" in names
        assert "tables/pathway_coverage/read_pathcoverage.tsv" in names
        assert "tables/ko_abundance/read_ko.tsv" in names
        assert "tables/ec_abundance/read_ec.tsv" in names
        assert "tables/mag_annotation.tsv" in names
        assert "tables/mag_function_annotation.tsv" in names
        assert "tables/sample_mag_abundance.tsv" in names
        assert "provenance/run.json" in names
        assert "provenance/software_versions.tsv" in names
        assert "provenance/input_checksums.tsv" in names
        assert "SHA256SUMS" in names
        assert "manifest.json" in names
        summary_payload = json.loads(
            handle.extractfile("reports/summary.json").read()
        )
        assert (
            summary_payload["entrypoints"]["multiqc_report"]
            == "reports/multiqc_report.html"
        )
        assert summary_payload["counts"]["species_rows"] == 1
        assert summary_payload["counts"]["gene_family_rows"] == 1
        assert summary_payload["counts"]["pathway_rows"] == 1
        assert summary_payload["counts"]["ko_abundance_files"] == 1
        assert summary_payload["counts"]["ec_abundance_files"] == 1
        assert summary_payload['counts']['figure_files'] == 3
        manifest_payload = json.loads(handle.extractfile("manifest.json").read())
        checksum_rows = {}
        for line in handle.extractfile("SHA256SUMS").read().decode().splitlines():
            checksum, relative = line.split("  ", 1)
            checksum_rows[relative] = checksum
        for record in manifest_payload["artifacts"]:
            relative = record["path"]
            payload = handle.extractfile(relative).read()
            assert record["size_bytes"] == len(payload)
            assert record["sha256"] == hashlib.sha256(payload).hexdigest()
            assert checksum_rows[relative] == record["sha256"]
        assert manifest_payload["checksum_algorithm"] == "sha256"
        assert manifest_payload["checksum_file"] == "SHA256SUMS"
        assert not any(name.endswith(".fastq.gz") for name in names)
        text_payload = "\n".join(
            handle.extractfile(name).read().decode("utf-8", errors="ignore")
            for name in names
            if not name.endswith(".zip")
        )
        assert str(nextflow_work) not in text_payload
        assert "/home/xh" not in text_payload
        assert "[NEXTFLOW_WORK]" in text_payload
        assert "[HOME]" in text_payload
        log_name = next(name for name in names if name.startswith("logs/"))
        log_text = handle.extractfile(log_name).read().decode()
        for secret in ("S01", "/home/xh", "topsecret", "219.224.3.96"):
            assert secret not in log_text
        assert "json-secret" not in log_text


def test_artifact_scan_allows_nextflow_work_symlink(tmp_path):
    module = load_module()
    state_root = tmp_path / "state"
    process_root = state_root / "work" / "aa" / "report-process"
    source_root = state_root / "work" / "bb" / "taxonomy-process"
    process_root.mkdir(parents=True)
    source_root.mkdir(parents=True)
    target = source_root / "species_abundance.tsv"
    target.write_text(
        "sample_id\ttaxonomy\tabundance\nS01\tE. coli\t0.9\n",
        encoding="utf-8",
    )
    staged = process_root / "species_abundance.tsv"
    staged.symlink_to(target)

    artifacts = module.scan_artifacts(process_root, state_root / "work")

    assert artifacts == [staged]


def test_artifact_scan_rejects_external_symlink(tmp_path):
    module = load_module()
    state_root = tmp_path / "state"
    process_root = state_root / "work" / "aa" / "report-process"
    process_root.mkdir(parents=True)
    outside = tmp_path / "patient-data.tsv"
    outside.write_text("sensitive\n", encoding="utf-8")
    (process_root / "species_abundance.tsv").symlink_to(outside)

    with pytest.raises(ValueError, match="escapes the approved work directory"):
        module.scan_artifacts(process_root, state_root / "work")


def test_artifact_scan_ignores_explicit_control_symlink(tmp_path):
    module = load_module()
    state_root = tmp_path / "state"
    process_root = state_root / "work" / "aa" / "report-process"
    process_root.mkdir(parents=True)
    external_manifest = tmp_path / "database.resolved.json"
    external_manifest.write_text('{"databases": {}}\n', encoding="utf-8")
    staged_manifest = process_root / "report-database.resolved.json"
    staged_manifest.symlink_to(external_manifest)

    artifacts = module.scan_artifacts(
        process_root,
        state_root / "work",
        [staged_manifest],
    )

    assert artifacts == []


def test_artifact_scan_ignores_nextflow_work_and_input_directories(tmp_path):
    module = load_module()
    results_root = tmp_path / "results"
    state_root = tmp_path / "state"
    outside = tmp_path / "patient.fastq.gz"
    outside.write_bytes(b"sensitive")
    (results_root / "work" / "aa").mkdir(parents=True)
    (results_root / "input").mkdir(parents=True)
    (results_root / "work" / "aa" / "species_abundance.tsv").symlink_to(outside)
    (results_root / "input" / "S01_R1.fastq.gz").symlink_to(outside)

    artifacts = module.scan_artifacts(results_root, state_root / "work")

    assert artifacts == []


def test_optional_tables_handle_collisions_and_empty_mag_functions(tmp_path):
    module = load_module()
    package_root = tmp_path / "package"
    (package_root / "tables").mkdir(parents=True)
    coverage_1 = tmp_path / "read_pathcoverage.tsv"
    coverage_2 = tmp_path / "read_pathcoverage.1.tsv"
    coverage_1.write_text("# Pathway\tS01\nPWY1\t0.8\n", encoding="utf-8")
    coverage_2.write_text("# Pathway\tS02\nPWY1\t0.7\n", encoding="utf-8")

    copied = module.copy_optional_artifact_set(
        [coverage_2, coverage_1],
        package_root / "tables" / "pathway_coverage",
        (),
    )

    assert copied == 2
    assert (package_root / "tables" / "pathway_coverage" / coverage_1.name).is_file()
    assert (package_root / "tables" / "pathway_coverage" / coverage_2.name).is_file()

    mag_function = tmp_path / "mag_function_annotation.tsv"
    mag_function.write_text(
        "mag_id\tfunction_id\tfunction_name\n",
        encoding="utf-8",
    )
    rows = module.combine_optional_standard_tables(
        [mag_function],
        package_root / "tables" / "mag_function_annotation.tsv",
        required_name="mag_function_annotation.tsv",
        required_fields=("mag_id", "function_id"),
    )

    assert rows == 0
    assert (
        package_root / "tables" / "mag_function_annotation.tsv"
    ).read_text(encoding="utf-8") == "mag_id\tfunction_id\tfunction_name\n"


def test_optional_artifact_set_rejects_empty_file(tmp_path):
    module = load_module()
    empty = tmp_path / "read_ko.tsv"
    empty.touch()

    with pytest.raises(ValueError, match="optional result artifact is empty"):
        module.copy_optional_artifact_set(
            [empty],
            tmp_path / "package" / "tables" / "ko_abundance",
            (),
        )


def test_optional_standard_table_rejects_invalid_required_columns(tmp_path):
    module = load_module()
    invalid = tmp_path / "mag_annotation.tsv"
    invalid.write_text(
        "mag_id\tquality_flag\nMAG_001\tmedium\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid required columns"):
        module.combine_optional_standard_tables(
            [invalid],
            tmp_path / "package" / "tables" / "mag_annotation.tsv",
            required_name="mag_annotation.tsv",
            required_fields=("mag_id", "taxonomy"),
        )


def test_optional_standard_tables_reject_mismatched_headers(tmp_path):
    module = load_module()
    first = tmp_path / "mag_annotation.tsv"
    second = tmp_path / "mag_annotation.1.tsv"
    first.write_text(
        "mag_id\ttaxonomy\nMAG_001\tBacteria\n",
        encoding="utf-8",
    )
    second.write_text(
        "mag_id\ttaxonomy\tquality_flag\nMAG_002\tArchaea\thigh\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="headers do not match"):
        module.combine_optional_standard_tables(
            [first, second],
            tmp_path / "package" / "tables" / "mag_annotation.tsv",
            required_name="mag_annotation.tsv",
            required_fields=("mag_id", "taxonomy"),
        )


def test_artifact_scan_ignores_staged_database_manifest(tmp_path):
    module = load_module()
    state_root = tmp_path / "state"
    process_root = state_root / "work" / "aa" / "report-process"
    provenance_root = state_root / "provenance"
    process_root.mkdir(parents=True)
    provenance_root.mkdir(parents=True)
    manifest = provenance_root / "database.resolved.json"
    manifest.write_text('{"resolved_manifest_sha256":"test"}\n', encoding="utf-8")
    (process_root / "database.resolved.json").symlink_to(manifest)

    artifacts = module.scan_artifacts(process_root, state_root / "work")

    assert artifacts == []


def test_artifact_scan_allows_nextflow_work_directory_symlink(tmp_path):
    module = load_module()
    state_root = tmp_path / "state"
    process_root = state_root / "work" / "aa" / "report-process"
    source_root = state_root / "work" / "bb" / "annotation-process"
    process_root.mkdir(parents=True)
    source_root.mkdir(parents=True)
    functions = source_root / "bin_funct_annotations"
    functions.mkdir()
    gff = functions / "bin.1.gff"
    gff.write_text("##gff-version 3\n", encoding="utf-8")
    (process_root / "bin_funct_annotations").symlink_to(functions)

    artifacts = module.scan_artifacts(process_root, state_root / "work")

    assert artifacts == [gff]


def test_redacted_log_copy_rejects_symlink(tmp_path):
    module = load_module()
    state_root = tmp_path / "state"
    log_root = state_root / "logs"
    destination = tmp_path / "package-logs"
    log_root.mkdir(parents=True)
    outside = tmp_path / "patient.txt"
    outside.write_text("patient secret\n", encoding="utf-8")
    (log_root / "S01.fastp.log").symlink_to(outside)

    with pytest.raises(ValueError, match="must not be a symlink"):
        module.copy_redacted_logs(
            state_root,
            destination,
            lambda text, **_: text,
            ["S01"],
            str(tmp_path),
        )


def test_packaged_log_read_is_bounded_to_tail(tmp_path):
    module = load_module()
    source = tmp_path / "large.log"
    source.write_bytes(
        b"discarded-secret\n"
        + (b"x" * 128)
        + b"boundary-secret\nvisible-tail"
    )

    text = module.read_bounded_log(source, max_bytes=64)

    assert text.startswith(module.TRUNCATED_LOG_NOTICE)
    assert "secret" not in text
    assert text.endswith("visible-tail")
    with pytest.raises(ValueError, match="positive"):
        module.read_bounded_log(source, max_bytes=0)
