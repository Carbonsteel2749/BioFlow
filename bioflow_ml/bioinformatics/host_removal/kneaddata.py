"""KneadData wrapper for host-read removal in metagenomic data."""

from __future__ import annotations

from typing import Any

from ..core import BaseBioTool, BioToolSpec


class KneadDataTool(BaseBioTool):
    spec = BioToolSpec("kneaddata", "0.1.0", "host_removal", "Host-contamination removal for microbiome reads", "kneaddata")

    def build_command(self, **params: Any) -> list[str]:
        read1, output_dir, reference_db = params.get("read1"), params.get("output_dir"), params.get("reference_db")
        if not read1 or not output_dir or not reference_db:
            raise ValueError("kneaddata requires read1, output_dir and reference_db")
        command = [self.spec.executable, "--input", self.path(read1)]
        if params.get("read2"):
            command.extend(["--input", self.path(params["read2"])])
        command.extend(["--output", self.path(output_dir), "--reference-db", self.path(reference_db), "--threads", str(int(params.get("threads", 4)))])
        return command
