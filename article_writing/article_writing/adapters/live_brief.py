"""Live BriefPort: user-supplied PaperBrief JSON."""

from __future__ import annotations

import json
from pathlib import Path

from article_writing.adapters.base import BriefPort
from article_writing.adapters.live_stubs import LiveAdapterNotReady
from article_writing.contracts import PaperBrief


class LiveBriefPort(BriefPort):
    def __init__(self, brief_path: str = "") -> None:
        self.brief_path = (brief_path or "").strip()

    def load(self) -> PaperBrief:
        if not self.brief_path:
            raise LiveAdapterNotReady(
                "LiveBriefPort.load has no brief_path. "
                "Provide a PaperBrief V1 JSON file."
            )
        path = Path(self.brief_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"brief JSON not found: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"brief JSON is invalid: {path}: {exc}") from exc
        try:
            return PaperBrief.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"brief JSON failed PaperBrief V1 validation: {path}") from exc
