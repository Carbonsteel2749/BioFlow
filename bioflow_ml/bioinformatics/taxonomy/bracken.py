"""Bracken wrapper for abundance re-estimation from a Kraken2 report."""

from __future__ import annotations

from typing import Any

from ..core import BaseBioTool, BioToolSpec


class BrackenTool(BaseBioTool):
    spec = BioToolSpec("bracken", "0.1.0", "abundance_estimation", "Taxonomic abundance re-estimation from a Kraken2 report", "bracken")

    def build_command(self, **params: Any) -> list[str]:
        database, report, output = params.get("database"), params.get("report"), params.get("output")
        if not all((database, report, output)):
            raise ValueError("bracken requires database, report and output")
        read_length = int(params.get("read_length", 150))
        if read_length < 1:
            raise ValueError("read_length must be positive")
        level = str(params.get("level", "S")).upper()
        if level not in {"D", "P", "C", "O", "F", "G", "S"}:
            raise ValueError("level must be one of D/P/C/O/F/G/S")
        return [self.spec.executable, "-d", self.path(database), "-i", self.path(report), "-o", self.path(output), "-r", str(read_length), "-l", level, "-t", str(int(params.get("threshold", 10)))]
