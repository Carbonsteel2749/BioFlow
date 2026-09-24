"""Kraken2 wrapper for metagenomic taxonomic classification."""

from __future__ import annotations

from typing import Any

from ..core import BaseBioTool, BioToolSpec


class Kraken2Tool(BaseBioTool):
    spec = BioToolSpec("kraken2", "0.1.0", "taxonomy", "Read-level taxonomic classification", "kraken2")

    def build_command(self, **params: Any) -> list[str]:
        database, read1, report, output = (params.get("database"), params.get("read1"), params.get("report"), params.get("output"))
        if not all((database, read1, report, output)):
            raise ValueError("kraken2 requires database, read1, report and output")
        command = [self.spec.executable, "--db", self.path(database), "--report", self.path(report), "--output", self.path(output), "--threads", str(int(params.get("threads", 4)))]
        if params.get("confidence") is not None:
            command.extend(["--confidence", str(float(params["confidence"]))])
        if params.get("read2"):
            command.extend(["--paired", self.path(read1), self.path(params["read2"])])
        else:
            command.append(self.path(read1))
        return command
