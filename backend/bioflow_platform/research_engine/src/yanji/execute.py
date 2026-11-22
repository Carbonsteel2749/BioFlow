"""A cancellable subprocess executes exactly one registered skill."""

import json
import sys
from pathlib import Path

from .db import initialize
from .skills.analysis import execute
from .skills.context import Context, clean_json


def main():
    initialize()
    spec = json.loads(Path(sys.argv[1]).read_text())
    if spec["step"]["skill"] == "report":
        from .reports import create_report

        report = create_report(
            spec["project_id"],
            spec["run_id"],
            spec["objective"],
            spec["completed"],
            spec["snapshot"],
        )
        output = {
            "skill": "report",
            "version": "1.0.0",
            "status": "success",
            "summary": "研究报告已生成。",
            "report_id": report["id"],
            "metrics": {},
            "evidence": [],
            "artifacts": [],
            "method": "已验证结果与证据编排。",
            "warnings": [],
        }
    else:
        context = Context(
            spec["project_id"],
            spec["run_id"],
            spec["step"]["id"],
            spec["snapshot"],
            spec["step"]["parameters"],
            spec.get("completed", []),
            spec["step"].get("inputs", {}),
        )
        if spec["step"]["skill"] == "custom_analysis":
            from .adaptive import execute as execute_program

            try:
                output = execute_program(context, spec["step"]["program"], spec["objective"])
            except ValueError as exc:
                Path(sys.argv[2]).write_text(
                    json.dumps({"error": str(exc)[:4000]}, ensure_ascii=False)
                )
                raise SystemExit(1) from None
        else:
            output = execute(context, spec["step"]["skill"])
    Path(sys.argv[2]).write_text(
        json.dumps(clean_json(output), ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
