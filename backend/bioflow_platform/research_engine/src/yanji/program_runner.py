"""Trusted bootstrap copied into an OS-isolated job; never imports application keys."""

import json
import os
import resource
import sys
import threading
import time
from pathlib import Path

resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2))
resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
parent = os.getppid()
root = Path(sys.argv[1]).resolve()


def watch_parent():
    while True:
        if os.getppid() != parent:
            os._exit(130)
        if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss > 2 * 1024**3:
            os._exit(137)
        output_files = list((root / "output").iterdir())
        if (
            len(output_files) > 50
            or sum(p.stat().st_size for p in output_files if p.is_file()) > 256 * 1024**2
        ):
            os._exit(137)
        time.sleep(0.5)


threading.Thread(target=watch_parent, daemon=True).start()

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

root = Path(sys.argv[1]).resolve()
manifest = json.loads((root / "input.json").read_text())
if manifest.get("font"):
    from matplotlib import font_manager

    font_manager.fontManager.addfont(manifest["font"])
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=manifest["font"]).get_name()
plt.rcParams["axes.unicode_minus"] = False
np.random.seed(42)
tables = {
    item["id"]: pd.read_json(
        root / item["file"],
        orient="split",
        dtype=False,
        convert_axes=False,
        convert_dates=False,
    )
    for item in manifest["tables"]
}
results = {
    item["id"]: pd.read_json(
        root / item["file"],
        orient="split",
        dtype=False,
        convert_axes=False,
        convert_dates=False,
    )
    for item in manifest.get("upstream", [])
}
outputs = {o["name"]: o for o in manifest["program"]["outputs"]}
requirements = {r["id"]: r for r in manifest["program"]["requirements"]}
records, checks = {}, {}


def emit_table(frame, name):
    output = outputs.get(name)
    if not output or output["kind"] != "table" or name in records:
        raise ValueError("结果表名称须与确认的方案一致，且不能重复：" + str(name))
    if not isinstance(frame, pd.DataFrame) or len(frame) > 1000000 or len(frame.columns) > 1000:
        raise ValueError("结果必须是规模可控的 DataFrame")
    if len(frame) < output["min_rows"] or not set(output["columns"]).issubset(frame.columns):
        raise ValueError("结果表缺少约定的列或行：" + name)
    filename = name + ".tsv"
    frame.to_csv(
        root / "output" / filename,
        sep="\t",
        index=True,
        index_label=frame.index.name or "ID",
    )
    records[name] = {
        "files": [filename],
        "rows": len(frame),
        "columns": list(map(str, frame.columns)),
        "kind": "table",
    }


def emit_chart(figure, name):
    output = outputs.get(name)
    if not output or output["kind"] != "chart" or name in records or not isinstance(figure, Figure):
        raise ValueError("图表须与确认的方案一致：" + str(name))
    files = []
    for suffix in ("png", "svg"):
        filename = name + "." + suffix
        figure.savefig(root / "output" / filename, bbox_inches="tight", dpi=160)
        files.append(filename)
    plt.close(figure)
    records[name] = {"files": files, "kind": "chart"}


def check(requirement_id, condition, detail):
    if requirement_id not in requirements:
        raise ValueError(f"未知研究要求 {requirement_id!r}；可用ID：{', '.join(requirements)}")
    passed = bool(condition)
    previous = checks.get(requirement_id, {"passed": True, "observations": []})
    observations = previous["observations"] + [{"passed": passed, "detail": str(detail)[:1000]}]
    checks[requirement_id] = {
        "passed": previous["passed"] and passed,
        "detail": str(detail)[:1000],
        "observations": observations,
    }
    if not passed:
        raise ValueError("研究要求未满足：" + requirement_id + " " + str(detail))


completed = None


def finish(summary, metrics=None, warnings=None):
    global completed
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("缺少实际计算的结论")
    if set(records) != set(outputs) or set(checks) != set(requirements):
        raise ValueError("尚未输出全部约定的产物或逐项验收检查")
    if not all(c["passed"] for c in checks.values()):
        raise ValueError("存在未通过的研究要求，后续检查不能覆盖先前失败")
    completed = {
        "summary": summary[:5000],
        "metrics": metrics or {},
        "warnings": warnings or [],
        "checks": checks,
        "outputs": records,
    }
    # Fail on non-finite result numbers rather than silently serializing NaN.
    encoded = json.dumps(completed, ensure_ascii=False, allow_nan=False)
    (root / "output" / "result.json").write_text(encoded)


code = (root / "program.py").read_text()
# OS sandbox is the security boundary. This namespace is an ergonomic computation API.
namespace = {
    "tables": tables,
    "results": results,
    "pd": pd,
    "np": np,
    "plt": plt,
    "emit_table": emit_table,
    "emit_chart": emit_chart,
    "check": check,
    "finish": finish,
}
try:
    exec(compile(code, "analysis.py", "exec"), namespace)
    if completed is None:
        raise ValueError("计算没有调用 finish，未形成完整分析结果")
except Exception as error:
    import traceback

    diagnostic = {
        "type": type(error).__name__,
        "message": str(error)[:4000],
        "checks": checks,
        "outputs": records,
        "traceback": traceback.format_exc()[-6000:],
    }
    (root / "output" / "failure.json").write_text(
        json.dumps(diagnostic, ensure_ascii=False, default=str)
    )
    raise
