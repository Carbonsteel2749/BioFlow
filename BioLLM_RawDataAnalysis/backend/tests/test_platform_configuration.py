import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATABASE_VALIDATOR = (
    PROJECT_ROOT / "workflow" / "bin" / "core" / "validate_databases.py"
)
PIPELINE_LAUNCHER = PROJECT_ROOT / "workflow" / "run_pipeline.sh"
MANIFEST_BASIS = "SHA256 of the sorted top-level file-name and byte-size inventory"


def _inventory_sha256(directory: Path) -> str:
    inventory = "".join(
        f"{path.name}\t{path.stat().st_size}\n"
        for path in sorted(directory.iterdir(), key=lambda item: item.name)
        if path.is_file()
    )
    return hashlib.sha256(inventory.encode()).hexdigest()


def _entry(
    name: str,
    directory: Path,
    *,
    taxonomy_system: str = "unclassified",
    sentinels: tuple[str, ...] = (),
) -> dict:
    return {
        "database_name": name,
        "purpose": f"{name} test database",
        "path": str(directory),
        "release": "test-r1",
        "taxonomy_system": taxonomy_system,
        "manifest_sha256": _inventory_sha256(directory),
        "manifest_basis": MANIFEST_BASIS,
        "tool_compatibility_version": "test-v1",
        "required_sentinel_files": list(sentinels),
    }


def _write_registry(root: Path, *, include_mags: bool = False) -> Path:
    directories = {
        name: root / name
        for name in (
            "kraken",
            "nucleotide",
            "protein",
            "utility",
            "metaphlan",
            "mag-classification",
            "mag-function",
        )
    }
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "database.sentinel").write_text("test\n", encoding="utf-8")
    for sentinel in ("hash.k2d", "opts.k2d", "taxo.k2d"):
        (directories["kraken"] / sentinel).write_text("test\n", encoding="utf-8")
    for sentinel in ("map_ko_uniref90.txt.gz", "map_level4ec_uniref90.txt.gz"):
        (directories["utility"] / sentinel).write_text("test\n", encoding="utf-8")

    kraken = _entry(
        "kraken2",
        directories["kraken"],
        taxonomy_system="NCBI",
        sentinels=("hash.k2d", "opts.k2d", "taxo.k2d"),
    )
    profile = {
        "taxonomy_reads": {"kraken2": kraken, "bracken": dict(kraken)},
        "function_reads": {
            "humann_nucleotide": _entry(
                "chocophlan",
                directories["nucleotide"],
                taxonomy_system="NCBI",
            ),
            "humann_protein": _entry("uniref", directories["protein"]),
            "humann_utility": _entry("humann-utility", directories["utility"]),
            "ko_mapping": _entry(
                "uniref90-to-ko",
                directories["utility"],
                sentinels=("map_ko_uniref90.txt.gz",),
            ),
            "ec_mapping": _entry(
                "uniref90-to-ec",
                directories["utility"],
                sentinels=("map_level4ec_uniref90.txt.gz",),
            ),
            "metaphlan": _entry(
                "metaphlan",
                directories["metaphlan"],
                taxonomy_system="MetaPhlAn SGB",
            ),
        },
        "mag_annotation": {},
    }
    if include_mags:
        profile["mag_annotation"] = {
            "classification": _entry(
                "mag-classification",
                directories["mag-classification"],
                taxonomy_system="GTDB",
            ),
            "function": _entry("mag-function", directories["mag-function"]),
        }
    registry = root / "database-registry.json"
    registry.write_text(
        json.dumps({"schema_version": 1, "profiles": {"test": profile}}, indent=2),
        encoding="utf-8",
    )
    return registry


def _run_validator(registry: Path, output: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "python3",
            str(DATABASE_VALIDATOR),
            "--registry",
            str(registry),
            "--profile",
            "test",
            "--output",
            str(output),
            *extra,
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_resolved_database_manifest_is_deterministic_and_not_rewritten(tmp_path: Path):
    registry = _write_registry(tmp_path)
    output = tmp_path / "database.resolved.json"

    first = _run_validator(registry, output)
    assert first.returncode == 0, first.stderr
    first_bytes = output.read_bytes()
    first_mtime = output.stat().st_mtime_ns
    time.sleep(0.02)
    second = _run_validator(registry, output)

    assert second.returncode == 0, second.stderr
    assert output.read_bytes() == first_bytes
    assert output.stat().st_mtime_ns == first_mtime


def test_database_inventory_change_is_rejected(tmp_path: Path):
    registry = _write_registry(tmp_path)
    output = tmp_path / "database.resolved.json"
    (tmp_path / "nucleotide" / "database.sentinel").write_text(
        "database changed\n",
        encoding="utf-8",
    )

    result = _run_validator(registry, output)

    assert result.returncode == 65
    assert "manifest SHA-256 mismatch" in result.stderr
    assert not output.exists()


def test_resolved_manifest_advertises_optional_mag_capability(tmp_path: Path):
    core_registry = _write_registry(tmp_path / "core")
    core_output = tmp_path / "core.resolved.json"
    mag_registry = _write_registry(tmp_path / "mag", include_mags=True)
    mag_output = tmp_path / "mag.resolved.json"

    assert _run_validator(core_registry, core_output).returncode == 0
    assert _run_validator(mag_registry, mag_output, "--enable-mags").returncode == 0

    core = json.loads(core_output.read_text(encoding="utf-8"))
    mags = json.loads(mag_output.read_text(encoding="utf-8"))
    assert core["capabilities"]["reads_analysis"] is True
    assert core["capabilities"]["mag_analysis"] is False
    assert mags["capabilities"]["mag_analysis"] is True
    assert set(mags["databases"]["mag_annotation"]) == {"classification", "function"}


def test_pipeline_launcher_passes_absolute_paths_to_nextflow(tmp_path: Path):
    registry = _write_registry(tmp_path / "db")
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    manifest = input_dir / "samples.csv"
    manifest.write_text("sample_id,read1,read2\n", encoding="utf-8")
    mock_nextflow = tmp_path / "mock-nextflow"
    recorded = tmp_path / "nextflow.args"
    mock_nextflow.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$MOCK_NEXTFLOW_ARGS\"\n",
        encoding="utf-8",
    )
    mock_nextflow.chmod(0o755)

    environment = os.environ.copy()
    environment["MOCK_NEXTFLOW_ARGS"] = str(recorded)
    result = subprocess.run(
        [
            "bash",
            str(PIPELINE_LAUNCHER),
            "--manifest",
            "input/samples.csv",
            "--outdir",
            "results",
            "--database-registry",
            "db/database-registry.json",
            "--database-profile",
            "test",
            "--nextflow-bin",
            str(mock_nextflow),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    arguments = recorded.read_text(encoding="utf-8").splitlines()
    for flag in (
        "--input_manifest",
        "--outdir",
        "--database_registry",
        "--database_manifest",
        "-work-dir",
        "-with-trace",
        "-with-timeline",
        "-with-report",
    ):
        value = arguments[arguments.index(flag) + 1]
        assert Path(value).is_absolute(), (flag, value)
