"""fastp wrapper for FASTQ quality control and adapter trimming."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import BaseBioTool, BioToolSpec


class FastpTool(BaseBioTool):
    spec = BioToolSpec("fastp", "0.1.0", "quality_control", "FASTQ quality control and adapter trimming", "fastp")

    def build_command(self, **params: Any) -> list[str]:
        read1, output1 = params.get("read1"), params.get("output1")
        read2, output2 = params.get("read2"), params.get("output2")
        if not read1 or not output1:
            raise ValueError("fastp requires read1 and output1")
        if bool(read2) != bool(output2):
            raise ValueError("paired-end fastp requires both read2 and output2")
        command = [self.spec.executable, "-i", self.path(read1), "-o", self.path(output1), "-w", str(int(params.get("threads", 4)))]
        if read2:
            command.extend(["-I", self.path(read2), "-O", self.path(output2)])
        if params.get("html_report"):
            command.extend(["-h", self.path(params["html_report"])])
        if params.get("json_report"):
            command.extend(["-j", self.path(params["json_report"])])
        return command
