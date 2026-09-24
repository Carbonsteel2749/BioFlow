from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..artifacts import ArtifactRepository


class ArtifactValidationError(ValueError):
    pass


class ArtifactIntegrityError(RuntimeError):
    pass


ARTIFACT_TYPES: dict[str, dict[str, Any]] = {
    "reads.raw": {"downloadable": False},
    "reads.clean": {"downloadable": False},
    "reads.host_removed": {"downloadable": False},
    "qc.read_counts": {"downloadable": True},
    "taxonomy.species_abundance": {"downloadable": True},
    "taxonomy.kraken_report": {"downloadable": True},
    "taxonomy.bracken_abundance": {"downloadable": True},
    "functional.gene_families": {"downloadable": True},
    "functional.ko_abundance": {"downloadable": True},
    "functional.ec_abundance": {"downloadable": True},
    "functional.pathway_abundance": {"downloadable": True},
    "mag.fasta": {"downloadable": True},
    "mag.annotation": {"downloadable": True},
    "mag.function_annotation": {"downloadable": True},
    "mag.sample_abundance": {"downloadable": True},
    "mag.sample_function_abundance": {"downloadable": True},
    "figure.qc": {"downloadable": True},
    "figure.taxonomy": {"downloadable": True},
    "figure.functional": {"downloadable": True},
    "figure.mag": {"downloadable": True},
    "figure.data": {"downloadable": True},
    "figure.catalog": {"downloadable": True},
    "report.multiqc": {"downloadable": True},
    "report.summary": {"downloadable": True},
    "provenance.run": {"downloadable": True},
}

SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAMPLE_SCOPES = {"global", "sample", "cohort"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ArtifactService:
    def __init__(self, repository: ArtifactRepository, output_root: Path):
        self.repository = repository
        self.output_root = Path(output_root).resolve()

    def register(
        self,
        *,
        task_id: str,
        producer: str,
        artifact_type: str,
        path: Path,
        media_type: str,
        node_id: str | None = None,
        schema_version: str = "1.0",
        sample_scope: str = "global",
        sample_id: str | None = None,
        cohort_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
        parent_artifact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self._validate_fields(
            task_id=task_id,
            producer=producer,
            artifact_type=artifact_type,
            schema_version=schema_version,
            sample_scope=sample_scope,
            sample_id=sample_id,
            cohort_id=cohort_id,
            media_type=media_type,
        )
        source = Path(path)
        if source.is_symlink():
            raise ArtifactValidationError("artifact symbolic links are not allowed")
        try:
            resolved = source.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ArtifactValidationError("artifact file does not exist") from exc
        if not resolved.is_file():
            raise ArtifactValidationError("artifact path must be a regular file")
        task_output = (self.output_root / task_id).resolve()
        if not resolved.is_relative_to(task_output):
            raise ArtifactValidationError("artifact must be inside the task output directory")

        size_bytes = resolved.stat().st_size
        sha256 = _sha256(resolved)
        if expected_size_bytes is not None and size_bytes != expected_size_bytes:
            raise ArtifactIntegrityError("artifact size does not match the declared size")
        if expected_sha256 is not None and sha256.lower() != expected_sha256.lower():
            raise ArtifactIntegrityError("artifact SHA-256 does not match the declared digest")

        parent_ids = sorted(set(parent_artifact_ids or []))
        for parent_id in parent_ids:
            parent = self.repository.get(parent_id)
            if parent is None:
                raise ArtifactValidationError(f"parent artifact not found: {parent_id}")
            if parent["task_id"] != task_id:
                raise ArtifactValidationError("derived artifacts must belong to the same task")

        record = {
            "artifact_id": str(uuid.uuid4()),
            "task_id": task_id,
            "node_id": node_id,
            "producer": producer,
            "artifact_type": artifact_type,
            "schema_version": schema_version,
            "sample_scope": sample_scope,
            "sample_id": sample_id,
            "cohort_id": cohort_id,
            "media_type": media_type,
            "path": str(resolved),
            "sha256": sha256,
            "size_bytes": size_bytes,
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            "downloadable": int(ARTIFACT_TYPES[artifact_type]["downloadable"]),
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.repository.insert(record, parent_ids)
        stored = self.repository.get(record["artifact_id"])
        return self._public(stored)

    def register_manifest(self, manifest_path: Path) -> list[dict[str, Any]]:
        source = Path(manifest_path)
        if source.is_symlink():
            raise ArtifactValidationError("artifact manifest symbolic links are not allowed")
        try:
            resolved_manifest = source.resolve(strict=True)
            if not resolved_manifest.is_relative_to(self.output_root):
                raise ArtifactValidationError(
                    "artifact manifest must be inside the output root"
                )
            payload = json.loads(resolved_manifest.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ArtifactValidationError("artifact manifest does not exist") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactValidationError("artifact manifest is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
            raise ArtifactValidationError("unsupported artifact manifest schema_version")
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not SAFE_IDENTIFIER.fullmatch(task_id):
            raise ArtifactValidationError("invalid task_id")
        task_output = (self.output_root / task_id).resolve()
        if not resolved_manifest.is_file() or not resolved_manifest.is_relative_to(task_output):
            raise ArtifactValidationError("artifact manifest must be inside the task output directory")
        entries = payload.get("artifacts")
        if not isinstance(entries, list) or not entries:
            raise ArtifactValidationError("artifact manifest must contain a non-empty artifacts list")

        registered: list[dict[str, Any]] = []
        supported_fields = {
            "producer",
            "artifact_type",
            "path",
            "media_type",
            "node_id",
            "schema_version",
            "sample_scope",
            "sample_id",
            "cohort_id",
            "metadata",
            "sha256",
            "size_bytes",
            "parent_artifact_ids",
        }
        for entry in entries:
            if not isinstance(entry, dict):
                raise ArtifactValidationError("artifact manifest entries must be objects")
            unknown = set(entry) - supported_fields
            if unknown:
                raise ArtifactValidationError(
                    "unsupported artifact manifest fields: " + ", ".join(sorted(unknown))
                )
            relative_path = entry.get("path")
            if not isinstance(relative_path, str) or not relative_path:
                raise ArtifactValidationError("artifact path is required")
            if Path(relative_path).is_absolute():
                raise ArtifactValidationError("artifact manifest paths must be relative")
            if "sha256" not in entry:
                raise ArtifactValidationError(
                    "artifact manifest entries must declare sha256"
                )
            if "size_bytes" not in entry:
                raise ArtifactValidationError(
                    "artifact manifest entries must declare size_bytes"
                )
            try:
                registered.append(
                    self.register(
                        task_id=task_id,
                        producer=entry["producer"],
                        artifact_type=entry["artifact_type"],
                        path=task_output / relative_path,
                        media_type=entry["media_type"],
                        node_id=entry.get("node_id"),
                        schema_version=entry.get("schema_version", "1.0"),
                        sample_scope=entry.get("sample_scope", "global"),
                        sample_id=entry.get("sample_id"),
                        cohort_id=entry.get("cohort_id"),
                        metadata=entry.get("metadata"),
                        expected_sha256=entry.get("sha256"),
                        expected_size_bytes=entry.get("size_bytes"),
                        parent_artifact_ids=entry.get("parent_artifact_ids"),
                    )
                )
            except KeyError as exc:
                raise ArtifactValidationError(
                    f"artifact manifest is missing required field: {exc.args[0]}"
                ) from exc
        return registered

    def get(self, artifact_id: str) -> dict[str, Any] | None:
        record = self.repository.get(artifact_id)
        return self._public(record) if record is not None else None

    def list_for_task(
        self,
        task_id: str,
        *,
        artifact_type: str | None = None,
        producer: str | None = None,
        sample_scope: str | None = None,
        figures_only: bool = False,
    ) -> list[dict[str, Any]]:
        if artifact_type is not None and artifact_type not in ARTIFACT_TYPES:
            raise ArtifactValidationError("unsupported artifact_type")
        if sample_scope is not None and sample_scope not in SAMPLE_SCOPES:
            raise ArtifactValidationError("unsupported sample_scope")
        records = self.repository.list_for_task(
            task_id,
            artifact_type=artifact_type,
            producer=producer,
            sample_scope=sample_scope,
            artifact_type_prefix="figure." if figures_only else None,
        )
        return [self._public(record) for record in records]

    def resolve_local(self, artifact_id: str) -> Path:
        record = self.repository.get(artifact_id)
        if record is None:
            raise KeyError(artifact_id)
        return self._verify_stored_file(record)

    def resolve_download(self, artifact_id: str) -> Path:
        record = self.repository.get(artifact_id)
        if record is None:
            raise KeyError(artifact_id)
        if not bool(record["downloadable"]):
            raise ArtifactValidationError("artifact is not downloadable")
        return self._verify_stored_file(record)

    def _verify_stored_file(self, record: dict[str, Any]) -> Path:
        path = Path(record["path"])
        if path.is_symlink():
            raise ArtifactIntegrityError("artifact changed after registration")
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError("artifact changed after registration") from exc
        task_output = (self.output_root / record["task_id"]).resolve()
        if not resolved.is_file() or not resolved.is_relative_to(task_output):
            raise ArtifactIntegrityError("artifact changed after registration")
        if resolved.stat().st_size != record["size_bytes"] or _sha256(resolved) != record["sha256"]:
            raise ArtifactIntegrityError("artifact changed after registration")
        return resolved

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": record["artifact_id"],
            "task_id": record["task_id"],
            "node_id": record["node_id"],
            "producer": record["producer"],
            "artifact_type": record["artifact_type"],
            "schema_version": record["schema_version"],
            "sample_scope": record["sample_scope"],
            "sample_id": record["sample_id"],
            "cohort_id": record["cohort_id"],
            "media_type": record["media_type"],
            "file_name": Path(record["path"]).name,
            "sha256": record["sha256"],
            "size_bytes": record["size_bytes"],
            "metadata": json.loads(record["metadata_json"]),
            "downloadable": bool(record["downloadable"]),
            "status": record["status"],
            "created_at": record["created_at"],
            "derived_from": record.get("derived_from", []),
            "download_url": (
                f"/api/artifacts/{record['artifact_id']}/download"
                if bool(record["downloadable"])
                else None
            ),
        }

    @staticmethod
    def _validate_fields(
        *,
        task_id: str,
        producer: str,
        artifact_type: str,
        schema_version: str,
        sample_scope: str,
        sample_id: str | None,
        cohort_id: str | None,
        media_type: str,
    ) -> None:
        if not SAFE_IDENTIFIER.fullmatch(task_id):
            raise ArtifactValidationError("invalid task_id")
        if not producer or len(producer) > 128:
            raise ArtifactValidationError("invalid producer")
        if artifact_type not in ARTIFACT_TYPES:
            raise ArtifactValidationError("unsupported artifact_type")
        if schema_version != "1.0":
            raise ArtifactValidationError("unsupported artifact schema_version")
        if sample_scope not in SAMPLE_SCOPES:
            raise ArtifactValidationError("unsupported sample_scope")
        if sample_scope == "sample" and not sample_id:
            raise ArtifactValidationError("sample_id is required for sample scope")
        if sample_scope == "cohort" and not cohort_id:
            raise ArtifactValidationError("cohort_id is required for cohort scope")
        if not media_type or len(media_type) > 128:
            raise ArtifactValidationError("invalid media_type")
