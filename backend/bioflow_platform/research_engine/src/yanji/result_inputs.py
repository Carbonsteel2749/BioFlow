"""Explicit, hash-checked table outputs passed between confirmed analysis steps."""

import hashlib
import re
from pathlib import Path

from . import db
from .config import runtime_root
from .data import DataError


def validate_bindings(plan):
    from .skills.registry import CATALOG

    seen = {}
    for step in plan.steps:
        for alias, ref in step.inputs.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", alias):
                raise DataError("结果输入别名须为小写英文、数字或下划线。")
            parent = seen.get(ref.step_id)
            if not parent or ref.step_id not in step.depends_on:
                raise DataError("结果输入必须引用前面的步骤，并明确声明依赖。")
            outputs = (
                [o.name for o in parent.program.outputs if o.kind == "table"]
                if parent.skill == "custom_analysis" and parent.program
                else CATALOG[parent.skill].outputs
            )
            if ref.output not in outputs:
                raise DataError(f"{ref.step_id} 没有声明可供后续分析使用的结果表：{ref.output}")
        seen[step.id] = step


def resolve(project_id, bindings, completed):
    resolved = {}
    for alias, ref in (bindings or {}).items():
        step_id = ref.step_id if hasattr(ref, "step_id") else ref["step_id"]
        output = ref.output if hasattr(ref, "output") else ref["output"]
        result = next((r for r in completed if r.get("step_id") == step_id), None)
        if not result or result.get("status") != "success":
            raise DataError(f"上游步骤尚未成功，不能读取 {alias}。")
        matches = [
            a
            for a in result.get("artifacts", [])
            if a["artifact_type"] == "table"
            and a.get("output_key", Path(a["filename"]).stem) == output
        ]
        if len(matches) != 1:
            raise DataError(f"上游结果表缺失或不唯一：{alias} / {output}")
        record = db.get(matches[0]["id"], "artifact")
        if record["project_id"] != project_id or record["sha256"] != matches[0]["sha256"]:
            raise DataError("结果输入不属于当前项目或版本不一致。")
        path = (runtime_root() / record["storage"]).resolve()
        if not path.is_relative_to(runtime_root()) or not path.is_file():
            raise DataError("结果输入文件不可用。")
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise DataError("结果输入内容已变化，不能复用旧的分析。")
        resolved[alias] = {
            "step_id": step_id,
            "output": output,
            "artifact_id": record["id"],
            "sha256": record["sha256"],
            "storage": record["storage"],
        }
    return resolved


def read_tables(resolved):
    import pandas as pd

    return {
        alias: pd.read_csv(
            runtime_root() / item["storage"],
            sep="\t",
            index_col=0,
            dtype=str,
            keep_default_na=False,
        )
        for alias, item in resolved.items()
    }


def describe_bindings(step, plan, datasets):
    """Declared schemas for code generation; never run upstream analysis in planning."""
    context = {}
    for alias, ref in step.inputs.items():
        parent = next(s for s in plan.steps if s.id == ref.step_id)
        item = {
            **ref.model_dump(),
            "producer": parent.skill,
            "parameters": parent.parameters.model_dump(),
        }
        if parent.program:
            item["columns"] = next(
                o.columns for o in parent.program.outputs if o.name == ref.output
            )
            from .adaptive import schema_context

            item["producer_method"] = parent.program.method
            item["producer_code"] = parent.program.code[:24000]
            item["source_structure"] = schema_context(
                [d for d in datasets if d["id"] in parent.program.inputs]
            )
        elif ref.output == "associations" and parent.skill in {
            "partial_correlation",
            "adjusted_regression",
        }:
            item["columns"] = [
                "predictor",
                "outcome",
                "effect",
                "metric",
                "n",
                "excluded",
                "controls",
                "ci_low",
                "ci_high",
                "p",
                "q",
                "status",
                "reason",
            ]
        elif ref.output == "associations":
            item["columns"] = [
                "predictor",
                "outcome",
                "effect",
                "metric",
                "n",
                "p",
                "q",
                "status",
                "reason",
                "rho",
            ]
            item["index"] = "feature"
            from .data import load_frame, taxonomy_rank

            source = next((d for d in datasets if d["id"] == parent.parameters.dataset_id), None)
            if source and source["data_type"] in {"taxonomy", "functional"}:
                labels = load_frame(source).index
                if source["data_type"] == "taxonomy":
                    labels = [
                        label for label in labels if taxonomy_rank(label) == parent.parameters.rank
                    ]
                item["key_examples"] = {"feature": list(map(str, labels[:5]))}
        elif ref.output == "correlation":
            item["columns"] = ["x", "y", "n", "rho", "p"]
        elif ref.output == "joined":
            from .data import load_frame

            chosen = [
                next((d for d in datasets if d["id"] == identity), None)
                for identity in [
                    parent.parameters.dataset_id,
                    parent.parameters.right_dataset_id,
                ]
            ]
            if all(chosen):
                left = [c for c in load_frame(chosen[0]).columns if c != parent.parameters.left_key]
                right = [
                    c for c in load_frame(chosen[1]).columns if c != parent.parameters.right_key
                ]
                item["columns"] = left + [c + "_right" if c in left else c for c in right]
        context[alias] = item
    return context
