from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from bioflow.core.models import ArtifactManifest, ResultBundle, RunProvenance


class NextflowRunner:
    def __init__(self, binary: str = "nextflow") -> None:
        self.binary = binary

    def build_command(self, pipeline: str, params: Dict[str, Any], work_dir: Path) -> List[str]:
        cmd = [self.binary, "run", pipeline, "-work-dir", str(work_dir)]
        for key, value in params.items():
            flag = f"--{key}"
            cmd.extend([flag, str(value)])
        return cmd

    def run(
        self,
        pipeline: str,
        params: Dict[str, Any],
        work_dir: Path,
        out_dir: Path,
    ) -> ResultBundle:
        started = datetime.utcnow().isoformat()
        cmd = self.build_command(pipeline, params, work_dir)
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        status = "success" if result.returncode == 0 else "failed"
        ended = datetime.utcnow().isoformat()
        provenance = RunProvenance(
            runner="nextflow",
            pipeline=pipeline,
            params=params,
            started_at=started,
            ended_at=ended,
            status=status,
            logs=[result.stdout, result.stderr],
        )
        artifacts = ArtifactManifest(root=str(out_dir))
        return ResultBundle(
            run_id=work_dir.name,
            provenance=provenance,
            artifacts=artifacts,
        )
