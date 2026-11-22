"""One durable queue consumer, with cancellation and restart recovery."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import select

from . import db
from .agent import fingerprint
from .config import runtime_root
from .skills.registry import CATALOG

STOP = False


def event(run_id, kind, message, **data):
    run = db.get(run_id, "run")
    return db.create(
        "event",
        run["project_id"],
        run_id=run_id,
        event_type=kind,
        message=message,
        **data,
    )


def cache_key(snapshot, step, completed=(), project_id=None):
    # Dataset ID pins immutable values; source hashes and transform metadata protect reuse semantics.
    datasets = [db.get(i, "dataset") for i in snapshot["datasets"].values()]
    inputs = [
        {
            "id": d["id"],
            "hash": d["source_sha256"],
            "transformations": d["transformations"],
        }
        for d in datasets
    ]
    from .result_inputs import resolve

    upstream = resolve(
        project_id or (datasets[0]["project_id"] if datasets else ""),
        step.get("inputs", {}),
        completed,
    )
    return fingerprint(
        {
            "inputs": inputs,
            "study": snapshot["study"],
            "understanding": snapshot.get("understanding", {}),
            "skill": step["skill"],
            "version": CATALOG[step["skill"]].version,
            "parameters": step["parameters"],
            "program": step.get("program"),
            "upstream": upstream,
            "preparation_version": "1.0.0",
        }
    )


def claim_next():
    with db.session() as session:
        # BEGIN IMMEDIATE serializes the claim across accidental extra worker processes.
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        records = session.scalars(
            select(db.Record).where(db.Record.kind == "run").order_by(db.Record.created_at)
        ).all()
        if any(r.data.get("status") == "running" for r in records):
            return None
        row = next((r for r in records if r.data.get("status") == "queued"), None)
        if row is None:
            return None
        row.data = {**row.data, "status": "running"}
        session.flush()
        return db.serialize(row)


def execute_step(run, step, completed, directory, i, total):
    """One failure must not discard successful work or unrelated branches."""
    run_id, skill = run["id"], step["skill"]
    key = cache_key(run["snapshot"], step, completed, run["project_id"])
    db.update(run_id, current_step=i, progress=round(i / total * 100))
    event(
        run_id,
        "step_started",
        f"正在执行：{CATALOG[skill].name}",
        step_id=step["id"],
    )
    cached = next(
        (c for c in reversed(db.list_records("cache", run["project_id"])) if c["cache_key"] == key),
        None,
    )
    if skill == "report":
        cached = None
    if cached and all(
        (runtime_root() / a["storage"]).is_file()
        and hashlib.sha256((runtime_root() / a["storage"]).read_bytes()).hexdigest() == a["sha256"]
        for a in cached["result"]["artifacts"]
    ):
        output = {
            **cached["result"],
            "reused_from": cached["run_id"],
            "step_id": step["id"],
        }
        # Evidence IDs remain unique within the current plan even after reuse.
        output["evidence"] = [
            {**e, "id": step["id"] + ":" + e["id"].split(":", 1)[-1]} for e in output["evidence"]
        ]
        event(
            run_id,
            "cache_hit",
            "输入和参数一致，复用已完成结果。",
            step_id=step["id"],
        )
    else:
        specpath = directory / f"{step['id']}.input.json"
        outpath = directory / f"{step['id']}.result.json"
        specpath.write_text(
            json.dumps(
                {
                    "project_id": run["project_id"],
                    "run_id": run_id,
                    "step": step,
                    "snapshot": run["snapshot"],
                    "completed": completed,
                    "objective": run["plan"]["objective"],
                },
                ensure_ascii=False,
            )
        )
        with (directory / f"{step['id']}.log").open("w") as log:
            env = {
                **os.environ,
                "MPLCONFIGDIR": str(runtime_root() / "matplotlib"),
                "PYTHONPATH": str(Path(__file__).resolve().parents[1])
                + os.pathsep
                + os.environ.get("PYTHONPATH", ""),
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            }
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "yanji.execute",
                    str(specpath),
                    str(outpath),
                ],
                stdout=log,
                stderr=log,
                env=env,
                start_new_session=True,
            )
            db.update(run_id, child_pid=process.pid, child_spec=str(specpath))
            start = time.monotonic()
            while process.poll() is None:
                if (
                    STOP
                    or db.get(run_id, "run").get("cancel_requested")
                    or time.monotonic() - start > 600
                ):
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    if time.monotonic() - start > 600:
                        raise RuntimeError("步骤超过十分钟限制，请缩小数据或参数后重试。")
                    db.update(run_id, status="interrupted" if STOP else "cancelled")
                    event(run_id, "stopped", "已取消当前步骤，已完成结果保留。")
                    return
                time.sleep(0.3)
        db.update(run_id, child_pid=None, child_spec=None)
        if process.returncode != 0:
            if skill == "custom_analysis" and outpath.is_file():
                failure = json.loads(outpath.read_text()).get("error")
                if failure:
                    raise RuntimeError("本次研究目标尚未完成：" + failure[:2000])
            raise RuntimeError(
                f"「{CATALOG[skill].name}」执行失败。详细错误已保存在本地运行日志，可修复后重试。"
            )
        output = json.loads(outpath.read_text())
        if output["status"] == "success" and skill != "report":
            db.create(
                "cache",
                run["project_id"],
                cache_key=key,
                run_id=run_id,
                result=output,
            )
    return output


def run_one(run):
    run_id = run["id"]
    directory = runtime_root() / "projects" / run["project_id"] / "runs" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    completed = list(run.get("results", []))
    steps = run["plan"]["steps"]
    event(run_id, "started", "开始执行已确认方案。")
    for i, step in enumerate(steps):
        latest = db.get(run_id, "run")
        if STOP or latest.get("cancel_requested"):
            db.update(run_id, status="interrupted" if STOP else "cancelled")
            event(run_id, "stopped", "运行已停止，已完成结果保留。")
            return
        if any(r.get("step_id") == step["id"] for r in completed):
            continue
        skill = step["skill"]
        blocked = [
            dep
            for dep in step.get("depends_on", [])
            if not any(r.get("step_id") == dep and r["status"] == "success" for r in completed)
        ]
        if skill == "report":
            blocked += [r["step_id"] for r in completed if r["status"] == "failed"]
        if blocked:
            completed.append(
                {
                    "skill": skill,
                    "step_id": step["id"],
                    "status": "skipped",
                    "summary": "上游步骤未完成，未执行。",
                    "method": "依赖检查",
                    "warnings": ["依赖：" + ", ".join(blocked)],
                    "metrics": {},
                    "artifacts": [],
                    "evidence": [],
                }
            )
            db.update(run_id, results=completed, progress=round((i + 1) / len(steps) * 100))
            event(run_id, "step_blocked", "上游条件未满足。", step_id=step["id"])
            continue
        try:
            output = execute_step(run, step, completed, directory, i, len(steps))
            if output is None:  # cancellation, never treated as analysis failure
                return
        except Exception as exc:
            detail = str(exc)[:8000]
            from .adaptive import explain_failure

            reason = explain_failure(detail)
            failure = {
                "step_id": step["id"],
                "message": reason,
                "detail": detail,
                "retryable": True,
            }
            output = {
                "skill": skill,
                "step_id": step["id"],
                "status": "failed",
                "summary": reason,
                "method": CATALOG[skill].name,
                "metrics": {},
                "warnings": [],
                "artifacts": [],
                "evidence": [],
                "failure": failure,
            }
            db.update(run_id, child_pid=None, child_spec=None)
            event(
                run_id,
                "step_failed",
                failure["message"],
                step_id=step["id"],
                detail=detail,
            )
        output["step_id"] = step["id"]
        completed.append(output)
        completed.sort(
            key=lambda r: next(j for j, s in enumerate(steps) if s["id"] == r["step_id"])
        )
        db.update(run_id, results=completed, progress=round((i + 1) / len(steps) * 100))
        event(
            run_id,
            "step_completed",
            output["summary"],
            step_id=step["id"],
            status=output["status"],
        )
    completed.sort(key=lambda r: next(j for j, s in enumerate(steps) if s["id"] == r["step_id"]))
    failures = [r["failure"] for r in completed if r["status"] == "failed"]
    db.update(
        run_id,
        status="failed" if failures else "completed",
        results=completed,
        failures=failures,
        partial=any(r["status"] != "success" for r in completed),
        progress=round(sum(r["status"] == "success" for r in completed) / len(steps) * 100)
        if failures
        else 100,
        error=f"{len(failures)} 个步骤尚未完成，已保留其他结果。修复后可从未完成处继续。"
        if failures
        else None,
    )
    event(
        run_id,
        "failed" if failures else "completed",
        "待修复步骤已记录，其他可执行步骤已完成。" if failures else "分析和报告已完成。",
    )


def recover():
    for run in db.list_records("run"):
        if run["status"] == "running":
            if run.get("child_pid") and run.get("child_spec"):
                try:
                    command = subprocess.check_output(
                        ["ps", "-p", str(run["child_pid"]), "-o", "command="], text=True
                    )
                    if "-m yanji.execute" in command and run["child_spec"] in command:
                        os.kill(run["child_pid"], signal.SIGTERM)
                except (OSError, subprocess.CalledProcessError):
                    pass
            db.update(
                run["id"],
                status="interrupted",
                error="服务重启，运行已中断；可继续使用已完成步骤。",
            )
            event(run["id"], "interrupted", "服务重启，等待用户选择继续。")


def main():
    global STOP
    db.initialize()
    # Kernel-backed lock prevents a second worker from recovering a live worker's jobs.
    import fcntl

    lock = (runtime_root() / "worker.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("研迹 worker 已在运行。")

    def stop(signum, frame):
        global STOP
        STOP = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    recover()
    while not STOP:
        run = claim_next()
        if run:
            try:
                run_one(run)
            except Exception as exc:
                db.update(run["id"], status="failed", error=str(exc)[:1000])
                event(run["id"], "failed", str(exc)[:1000])
        else:
            time.sleep(0.5)


if __name__ == "__main__":
    main()
