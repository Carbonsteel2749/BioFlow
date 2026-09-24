from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from bioflow.core.models import ArtifactManifest, ResultBundle, RunProvenance


class MockNextflowRunner:
    def __init__(self, fixture: Optional[str] = None) -> None:
        self.fixture = fixture

    def _load_fixture(self) -> Dict[str, Any]:
        if not self.fixture:
            return {}
        path = Path(self.fixture)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def run(
        self,
        pipeline: str,
        params: Dict[str, Any],
        work_dir: Path,
        out_dir: Path,
    ) -> ResultBundle:
        fixture = self._load_fixture()
        started = datetime.utcnow().isoformat()
        ended = datetime.utcnow().isoformat()
        provenance = RunProvenance(
            runner="mock",
            pipeline=pipeline or fixture.get("pipeline", "mock"),
            params=params,
            started_at=started,
            ended_at=ended,
            status="success",
            logs=["mock run"],
        )
        artifacts = ArtifactManifest(root=str(out_dir))
        return ResultBundle(
            run_id=work_dir.name,
            provenance=provenance,
            artifacts=artifacts,
            metrics=fixture.get("metrics", {}),
            tables=fixture.get("tables", []),
            figures=fixture.get("figures", []),
            notes=fixture.get("notes", []),
        )
