import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    input_root: Path
    state_root: Path
    workflow_path: Path
    nextflow_bin: str = "nextflow"
    auto_run: bool = True
    poll_interval_seconds: float = 1.0
    default_host_index: Path | None = None
    default_kraken_db: Path | None = None
    default_humann_nucleotide_db: Path | None = None
    default_humann_protein_db: Path | None = None
    default_metaphlan_db: Path | None = None
    default_database_registry: Path | None = None
    default_database_profile: str | None = None
    default_database_manifest: Path | None = None
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:14b"
    ollama_timeout_seconds: float = 60.0
    auto_retry_enabled: bool = False
    retry_minimum_confidence: float = 0.90
    retry_validation_manifest: Path | None = None
    retry_validation_timeout_seconds: float = 7200.0
    max_upload_bytes: int = 250 * 1024 * 1024 * 1024

    @property
    def database_path(self) -> Path:
        return self.state_root / "state" / "tasks.sqlite3"

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def prepare(self) -> None:
        self.input_root.mkdir(parents=True, exist_ok=True)
        for relative in ("state", "logs", "work", "outputs"):
            (self.state_root / relative).mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = Path(__file__).resolve().parents[2]
        host_index = os.environ.get("BIOLLM_HOST_INDEX", "").strip()
        kraken_db = os.environ.get("BIOLLM_KRAKEN_DB", "").strip()
        humann_nucleotide_db = os.environ.get("BIOLLM_HUMANN_NUCLEOTIDE_DB", "").strip()
        humann_protein_db = os.environ.get("BIOLLM_HUMANN_PROTEIN_DB", "").strip()
        metaphlan_db = os.environ.get("BIOLLM_METAPHLAN_DB", "").strip()
        database_registry = os.environ.get("BIOLLM_DATABASE_REGISTRY", "").strip()
        database_profile = os.environ.get("BIOLLM_DATABASE_PROFILE", "").strip()
        database_manifest = os.environ.get("BIOLLM_DATABASE_MANIFEST", "").strip()
        retry_validation_manifest = os.environ.get(
            "BIOLLM_RETRY_VALIDATION_MANIFEST", ""
        ).strip()
        return cls(
            input_root=Path(os.environ.get("BIOLLM_INPUT_ROOT", project_root / "runtime" / "incoming")),
            state_root=Path(os.environ.get("BIOLLM_STATE_ROOT", project_root / "runtime")),
            workflow_path=Path(os.environ.get("BIOLLM_WORKFLOW_PATH", project_root / "workflow" / "main.nf")),
            nextflow_bin=os.environ.get("BIOLLM_NEXTFLOW_BIN", "nextflow"),
            auto_run=os.environ.get("BIOLLM_AUTO_RUN", "1").lower() not in {"0", "false", "no"},
            poll_interval_seconds=float(os.environ.get("BIOLLM_POLL_INTERVAL", "1.0")),
            default_host_index=Path(host_index) if host_index else None,
            default_kraken_db=Path(kraken_db) if kraken_db else None,
            default_humann_nucleotide_db=(
                Path(humann_nucleotide_db) if humann_nucleotide_db else None
            ),
            default_humann_protein_db=(
                Path(humann_protein_db) if humann_protein_db else None
            ),
            default_metaphlan_db=Path(metaphlan_db) if metaphlan_db else None,
            default_database_registry=(
                Path(database_registry) if database_registry else None
            ),
            default_database_profile=database_profile or None,
            default_database_manifest=(
                Path(database_manifest) if database_manifest else None
            ),
            ollama_url=os.environ.get(
                "BIOLLM_OLLAMA_URL", "http://127.0.0.1:11434"
            ),
            ollama_model=os.environ.get("BIOLLM_OLLAMA_MODEL", "qwen3:14b"),
            ollama_timeout_seconds=float(
                os.environ.get("BIOLLM_OLLAMA_TIMEOUT", "60")
            ),
            auto_retry_enabled=os.environ.get(
                "BIOLLM_AUTO_RETRY", "0"
            ).lower()
            in {"1", "true", "yes"},
            retry_minimum_confidence=float(
                os.environ.get("BIOLLM_RETRY_MIN_CONFIDENCE", "0.90")
            ),
            retry_validation_manifest=(
                Path(retry_validation_manifest)
                if retry_validation_manifest
                else None
            ),
            retry_validation_timeout_seconds=float(
                os.environ.get("BIOLLM_RETRY_VALIDATION_TIMEOUT", "7200")
            ),
            max_upload_bytes=int(
                os.environ.get("BIOLLM_MAX_UPLOAD_BYTES", str(250 * 1024 * 1024 * 1024))
            ),
        )
