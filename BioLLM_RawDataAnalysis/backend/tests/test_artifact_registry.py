import json
from pathlib import Path

import pytest

from backend.app.artifacts import ArtifactRepository
from backend.app.services.artifacts import (
    ARTIFACT_TYPES,
    ArtifactIntegrityError,
    ArtifactService,
    ArtifactValidationError,
)


@pytest.fixture
def registry(tmp_path: Path):
    output_root = tmp_path / "outputs"
    repository = ArtifactRepository(tmp_path / "state.sqlite3")
    repository.initialize()
    return ArtifactService(repository, output_root), output_root


def _write_result(output_root: Path, task_id: str, name: str, content: bytes) -> Path:
    result = output_root / task_id / name
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_bytes(content)
    return result


def test_register_and_query_artifact_without_exposing_local_path(registry):
    service, output_root = registry
    result = _write_result(output_root, "task-1", "tables/read_counts.tsv", b"sample\treads\nS01\t10\n")

    artifact = service.register(
        task_id="task-1",
        producer="fastqc_raw",
        artifact_type="qc.read_counts",
        path=result,
        media_type="text/tab-separated-values",
        sample_scope="sample",
        sample_id="S01",
        metadata={"unit": "reads"},
    )

    assert artifact["artifact_id"]
    assert "path" not in artifact
    assert artifact["downloadable"] is True
    assert artifact["metadata"] == {"unit": "reads"}
    assert service.list_for_task("task-1") == [artifact]


def test_registration_rejects_outside_path_symlink_and_bad_digest(registry, tmp_path: Path):
    service, output_root = registry
    outside = tmp_path / "outside.tsv"
    outside.write_text("outside", encoding="utf-8")

    with pytest.raises(ArtifactValidationError, match="task output directory"):
        service.register(
            task_id="task-1",
            producer="taxonomy",
            artifact_type="taxonomy.species_abundance",
            path=outside,
            media_type="text/tab-separated-values",
        )

    result = _write_result(output_root, "task-1", "tables/species.tsv", b"species\tabundance\n")
    symlink = output_root / "task-1" / "tables" / "species-link.tsv"
    try:
        symlink.symlink_to(result)
    except OSError:
        pytest.skip("symlinks are not available")
    with pytest.raises(ArtifactValidationError, match="symbolic links"):
        service.register(
            task_id="task-1",
            producer="taxonomy",
            artifact_type="taxonomy.species_abundance",
            path=symlink,
            media_type="text/tab-separated-values",
        )

    with pytest.raises(ArtifactIntegrityError, match="SHA-256"):
        service.register(
            task_id="task-1",
            producer="taxonomy",
            artifact_type="taxonomy.species_abundance",
            path=result,
            media_type="text/tab-separated-values",
            expected_sha256="0" * 64,
        )


def test_restricted_reads_can_be_registered_but_not_downloaded(registry):
    service, output_root = registry
    reads = _write_result(output_root, "task-1", "reads/S01_R1.fastq.gz", b"reads")
    artifact = service.register(
        task_id="task-1",
        producer="host_depletion",
        artifact_type="reads.host_removed",
        path=reads,
        media_type="application/gzip",
        sample_scope="sample",
        sample_id="S01",
    )

    assert artifact["downloadable"] is False
    with pytest.raises(ArtifactValidationError, match="not downloadable"):
        service.resolve_download(artifact["artifact_id"])


def test_download_rechecks_file_integrity(registry):
    service, output_root = registry
    result = _write_result(output_root, "task-1", "figures/qc.png", b"png-v1")
    artifact = service.register(
        task_id="task-1",
        producer="plot_qc",
        artifact_type="figure.qc",
        path=result,
        media_type="image/png",
    )
    assert service.resolve_download(artifact["artifact_id"]) == result.resolve()

    result.write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError, match="changed after registration"):
        service.resolve_download(artifact["artifact_id"])


def test_relations_and_filters_are_preserved(registry):
    service, output_root = registry
    source = _write_result(output_root, "task-1", "tables/read_counts.tsv", b"counts")
    source_artifact = service.register(
        task_id="task-1",
        producer="fastqc_raw",
        artifact_type="qc.read_counts",
        path=source,
        media_type="text/tab-separated-values",
    )
    figure = _write_result(output_root, "task-1", "figures/qc.png", b"figure")
    figure_artifact = service.register(
        task_id="task-1",
        producer="plot_qc",
        artifact_type="figure.qc",
        path=figure,
        media_type="image/png",
        parent_artifact_ids=[source_artifact["artifact_id"]],
    )

    assert figure_artifact["derived_from"] == [source_artifact["artifact_id"]]
    assert service.list_for_task("task-1", artifact_type="figure.qc") == [figure_artifact]
    assert service.list_for_task("task-1", producer="fastqc_raw") == [source_artifact]


def test_register_manifest_uses_relative_paths_and_declared_integrity(registry):
    service, output_root = registry
    result = _write_result(output_root, "task-1", "tables/read_counts.tsv", b"counts")
    manifest = output_root / "task-1" / "artifact-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "task_id": "task-1",
                "artifacts": [
                    {
                        "producer": "fastqc_raw",
                        "artifact_type": "qc.read_counts",
                        "path": "tables/read_counts.tsv",
                        "media_type": "text/tab-separated-values",
                        "sha256": "cdd93696b72b3de32dd12e38bdcae2eaf5a95080af38cbe347f3195ef9d56bf7",
                        "size_bytes": 6,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    registered = service.register_manifest(manifest)

    assert len(registered) == 1
    assert registered[0]["artifact_type"] == "qc.read_counts"
    assert registered[0]["file_name"] == result.name


def test_register_manifest_rejects_absolute_artifact_paths(registry):
    service, output_root = registry
    result = _write_result(output_root, "task-1", "tables/read_counts.tsv", b"counts")
    manifest = output_root / "task-1" / "artifact-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "task_id": "task-1",
                "artifacts": [
                    {
                        "producer": "fastqc_raw",
                        "artifact_type": "qc.read_counts",
                        "path": str(result),
                        "media_type": "text/tab-separated-values",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactValidationError, match="relative"):
        service.register_manifest(manifest)


def test_register_manifest_requires_declared_integrity(registry):
    service, output_root = registry
    _write_result(output_root, "task-1", "tables/read_counts.tsv", b"counts")
    manifest = output_root / "task-1" / "artifact-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "task_id": "task-1",
                "artifacts": [
                    {
                        "producer": "fastqc_raw",
                        "artifact_type": "qc.read_counts",
                        "path": "tables/read_counts.tsv",
                        "media_type": "text/tab-separated-values",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactValidationError, match="sha256"):
        service.register_manifest(manifest)


def test_artifact_manifest_schema_matches_runtime_type_catalog():
    schema_path = Path(__file__).resolve().parents[2] / "workflow" / "assets" / "artifact.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["required"] == ["schema_version", "task_id", "artifacts"]
    declared_types = schema["$defs"]["artifact"]["properties"]["artifact_type"]["enum"]
    assert set(declared_types) == set(ARTIFACT_TYPES)
