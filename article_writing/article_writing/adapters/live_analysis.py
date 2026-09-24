"""Live AnalysisPort: BioLLM result package / archive / API → AnalysisBundle."""

from __future__ import annotations

import json
import re
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from article_writing.adapters.base import AnalysisPort
from article_writing.adapters.biollm_mapping import (
    BioLLMPackageError,
    locate_package_root,
    map_biollm_package,
)
from article_writing.adapters.live_stubs import LiveAdapterNotReady
from article_writing.contracts import AnalysisBundle

SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class LiveAnalysisPort(AnalysisPort):
    """Load a real analysis package without touching section writers."""

    def __init__(
        self,
        bundle_path: str = "",
        run_dir: str = "",
        analysis_url: str = "",
        task_id: str = "",
        timeout: float = 60.0,
    ) -> None:
        self.bundle_path = (bundle_path or "").strip()
        self.run_dir = (run_dir or "").strip()
        self.analysis_url = (analysis_url or "").rstrip("/")
        self.task_id = (task_id or "").strip()
        self.timeout = timeout
        self.package_root: Path | None = None
        self._temp_dirs: list[tempfile.TemporaryDirectory[str]] = []

    def load(self) -> AnalysisBundle:
        if self.run_dir:
            root = locate_package_root(self.run_dir)
            self.package_root = root
            return map_biollm_package(root)
        if self.bundle_path:
            return self._load_bundle_path(Path(self.bundle_path).expanduser())
        if self.analysis_url and self.task_id:
            archive = self._download_task_archive()
            return self._load_archive(archive)
        raise LiveAdapterNotReady(
            "LiveAnalysisPort.load has no source. Provide analysis_run_dir, "
            "analysis_bundle (JSON or tar.gz), or analysis_url + task_id. "
            "Keep returning article_writing.contracts V1 models only."
        )

    def _load_bundle_path(self, path: Path) -> AnalysisBundle:
        if not path.exists():
            raise FileNotFoundError(f"analysis bundle not found: {path}")
        if path.is_dir():
            root = locate_package_root(path)
            self.package_root = root
            return map_biollm_package(root)
        suffix = "".join(path.suffixes).lower()
        if path.suffix.lower() == ".json":
            return self._load_analysis_json(path)
        if suffix.endswith(".tar.gz") or path.suffix.lower() in {".tgz", ".tar"}:
            return self._load_archive(path)
        raise BioLLMPackageError(
            f"unsupported analysis bundle type: {path} "
            "(expected AnalysisBundle JSON, directory, or .tar.gz)"
        )

    def _load_analysis_json(self, path: Path) -> AnalysisBundle:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise BioLLMPackageError(f"analysis JSON must be an object: {path}")
        try:
            bundle = AnalysisBundle.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 - surface mapping failures clearly
            raise BioLLMPackageError(
                f"analysis JSON is not an AnalysisBundle V1 object: {path}"
            ) from exc
        raw_root = ""
        if isinstance(bundle.raw, dict):
            raw_root = str(bundle.raw.get("package_root") or "")
        if raw_root and Path(raw_root).is_dir():
            self.package_root = Path(raw_root)
        return bundle

    def _load_archive(self, archive: Path) -> AnalysisBundle:
        if not archive.is_file():
            raise FileNotFoundError(f"analysis archive not found: {archive}")
        extracted = self._make_temp_dir()
        _safe_extract(archive, extracted)
        root = locate_package_root(extracted)
        self.package_root = root
        return map_biollm_package(root)

    def _make_temp_dir(self) -> Path:
        handle = tempfile.TemporaryDirectory(prefix="article-writing-biollm-")
        self._temp_dirs.append(handle)
        return Path(handle.name)

    def _download_task_archive(self) -> Path:
        if not SAFE_TASK_ID.fullmatch(self.task_id) or ".." in self.task_id:
            raise BioLLMPackageError(f"unsafe analysis task id: {self.task_id!r}")
        url = f"{self.analysis_url}/api/tasks/{self.task_id}/results"
        destination = self._make_temp_dir() / f"{self.task_id}.tar.gz"
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                with destination.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
        except OSError as exc:
            raise BioLLMPackageError(
                f"failed to download BioLLM result archive from {url}: {exc}"
            ) from exc
        if destination.stat().st_size == 0:
            raise BioLLMPackageError(f"downloaded archive is empty: {url}")
        return destination


def _safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as handle:
        for member in handle.getmembers():
            name = Path(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise BioLLMPackageError(f"unsafe archive member: {member.name}")
        handle.extractall(destination)
