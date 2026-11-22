import os
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def runtime_root() -> Path:
    path = Path(os.environ.get("YANJI_RUNTIME", ROOT / "runtime")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_config() -> dict:
    cfg = dotenv_values(ROOT / ".env")
    return {
        "api_key": os.environ.get("YANJI_API_KEY", cfg.get("api_key", "") or ""),
        "base_url": os.environ.get("YANJI_BASE_URL", cfg.get("base_url", "") or "").rstrip("/"),
        "model": os.environ.get(
            "YANJI_MODEL", cfg.get("model", "qwen3.8-flash") or "qwen3.8-flash"
        ),
    }


MAX_UPLOAD = 100 * 1024**2
MAX_CELLS = 10_000_000
FONT = ROOT / "assets/fonts/NotoSansSC.ttf"
