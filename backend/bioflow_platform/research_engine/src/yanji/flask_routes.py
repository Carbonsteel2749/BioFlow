"""Flask Blueprint routes. Numerical services remain independent of Flask."""

import csv
import json
import time
from functools import wraps
from pathlib import Path

from flask import Blueprint, Response, request, send_file

from . import agent, data, db
from .config import MAX_UPLOAD, model_config, runtime_root
from .contracts import (
    AnalysisPlan,
    ChatRequest,
    ClarifyRequest,
    DatasetVersion,
    ImportOptions,
    MappingUpdate,
    MeaningRequest,
    ProjectCreate,
    ReportUpdate,
    RunConfirm,
    SkillResult,
    StrictModel,
    StudyConfig,
    UnderstandingUpdate,
)
from .reports import artifact_path, export
from .skills.registry import catalog
from .writing_support import LiteratureRequest, WritingRequest

bp = Blueprint("api", __name__)


class HTTPException(Exception):
    def __init__(self, status_code=500, detail="请求失败"):
        self.status_code, self.detail = status_code, detail


def created(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        return fn(*args, **kwargs), 201

    return wrapped


def FileResponse(path, filename=None, media_type=None):
    return send_file(
        path, download_name=filename, as_attachment=bool(filename), mimetype=media_type
    )


def belongs(record_id, project_id, kind):
    record = db.get(record_id, kind)
    if record["project_id"] != project_id:
        raise HTTPException(404, "记录不属于当前项目。")
    return record


@bp.route("/api/v1/health", methods=["GET"])
def health():
    return {"status": "ok", "application": "yanji", "version": "1.0.0"}


@bp.route("/api/v1/contracts", methods=["GET"])
def contracts():
    return {
        "DatasetVersion": DatasetVersion.model_json_schema(),
        "SkillResult": SkillResult.model_json_schema(),
        "AnalysisPlan": AnalysisPlan.model_json_schema(),
    }


@bp.route("/api/v1/projects", methods=["GET"])
def projects():
    return [p for p in db.list_records("project") if not p.get("archived")]


@bp.route("/api/v1/projects", methods=["POST"])
@created
def create_project():
    body = ProjectCreate.model_validate(request.get_json(silent=True) or {})
    return db.create(
        "project",
        name=body.name.strip(),
        description=body.description,
        active_datasets={},
        material_ids=[],
        study={"group_column": None, "independent": False, "excluded_samples": []},
        archived=False,
    )


@bp.put("/api/v1/projects/<pid>")
def edit_project(pid):
    db.get(pid, "project")
    body = ProjectCreate.model_validate(request.get_json(silent=True) or {})
    return db.update(pid, name=body.name, description=body.description)


@bp.route("/api/v1/projects/<pid>", methods=["GET"])
def project_detail(pid: str):
    project = db.get(pid, "project")
    reports = db.list_records("report", pid)
    reports = [{**r, "stale": report_stale(pid, r)} for r in reports]
    datasets = data.materials(pid)
    metadata = next((d for d in datasets if d["data_type"] == "metadata"), None)
    group_counts = {}
    column = project.get("study", {}).get("group_column")
    if metadata and column in metadata["columns"]:
        frame = data.load_frame(metadata)
        group_counts = frame[column].astype(str).str.strip().value_counts().to_dict()
    reference = next((d for d in datasets if d["data_type"] == "taxonomy"), None)
    if reference is None:
        reference = next((d for d in datasets if d["data_type"] == "functional"), None)
    datasets = [
        {
            **d,
            "alignment": {
                "matched": len(set(d["samples"]) & set(reference["samples"]))
                if reference
                else len(d["samples"]),
                "reference_samples": len(reference["samples"]) if reference else len(d["samples"]),
                "needs_confirmation": data.mapping_suggestions(pid, d)["needs_confirmation"],
            },
        }
        for d in datasets
    ]
    return {
        **project,
        "datasets": datasets,
        "group_counts": group_counts,
        "context": __import__("yanji.project_context", fromlist=["describe"]).describe(pid),
        "runs": db.list_records("run", pid),
        "reports": reports,
        "messages": db.list_records("message", pid),
        "plans": db.list_records("plan", pid),
    }


@bp.route("/api/v1/projects/<pid>", methods=["DELETE"])
def archive_project(pid: str):
    db.get(pid, "project")
    if any(run["status"] in {"queued", "running"} for run in db.list_records("run", pid)):
        raise HTTPException(409, "项目还有正在执行或等待中的分析，请先停止分析再删除。")
    return db.update(pid, archived=True)


@bp.route("/api/v1/projects/<pid>/restore", methods=["POST"])
def restore_project(pid: str):
    db.get(pid, "project")
    return db.update(pid, archived=False)


@bp.route("/api/v1/projects/<pid>/study", methods=["PUT"])
def configure_study(pid: str):
    body = StudyConfig.model_validate(request.get_json(silent=True) or {})
    ds = data.active(pid)
    if body.group_column:
        md = next((d for d in ds if d["data_type"] == "metadata"), None)
        if not md or body.group_column not in md["columns"]:
            raise data.DataError("分组列不存在，请先上传样本信息表。")
    samples = {s for d in ds for s in d["samples"]}
    if not set(body.excluded_samples).issubset(samples):
        raise data.DataError("排除列表包含未知样本。")
    return db.update(pid, study=body.model_dump())


@bp.route("/api/v1/projects/<pid>/datasets/<did>", methods=["GET"])
def dataset_detail(pid: str, did: str):
    d = belongs(did, pid, "dataset")
    from .understanding import describe

    return {
        **d,
        "preview": data.preview(d),
        "suggestions": data.mapping_suggestions(pid, d),
        "field_profile": describe(d),
    }


@bp.put("/api/v1/projects/<pid>/datasets/<did>/understanding")
def update_understanding(pid, did):
    from .understanding import save

    return save(
        belongs(did, pid, "dataset"),
        UnderstandingUpdate.model_validate(request.get_json(silent=True) or {}),
    )


@bp.post("/api/v1/projects/<pid>/datasets/<did>/understanding/suggest")
def suggest_understanding(pid, did):
    from .understanding import suggest

    body = MeaningRequest.model_validate(request.get_json(silent=True) or {})
    return suggest(belongs(did, pid, "dataset"), body.description)


@bp.post("/api/v1/projects/<pid>/datasets/<did>/reinterpret")
def reinterpret_dataset(pid, did):
    old = belongs(did, pid, "dataset")
    opts = ImportOptions.model_validate(request.get_json(silent=True) or {})
    if opts.kind == "auto":
        raise data.DataError("请选择数据用途。")
    path = data.project_dir(pid) / "datasets" / old["source_id"] / "source.txt"
    if not path.is_file():
        raise data.DataError("原始文件不可用，请重新上传。")
    newer = data.import_bytes(pid, old["filename"], path.read_bytes(), opts)
    newer = db.update(newer["id"], revision=old["revision"] + 1, parent_version=old["id"])
    p = db.get(pid, "project")
    db.update(
        pid,
        active_datasets={k: v for k, v in p["active_datasets"].items() if v != did},
        material_ids=[i for i in p.get("material_ids", []) if i != did],
    )
    return newer


@bp.route("/api/v1/projects/<pid>/datasets/<did>/mapping", methods=["POST"])
@created
def confirm_mapping(pid: str, did: str):
    body = MappingUpdate.model_validate(request.get_json(silent=True) or {})
    belongs(did, pid, "dataset")
    return data.remap(did, body.mapping, body.excluded_samples)


@bp.route("/api/v1/projects/<pid>/datasets/<did>", methods=["DELETE"])
def deactivate_dataset(pid: str, did: str):
    belongs(did, pid, "dataset")
    p = db.get(pid, "project")
    return db.update(
        pid,
        active_datasets={k: v for (k, v) in p["active_datasets"].items() if v != did},
        material_ids=[d["id"] for d in data.materials(pid) if d["id"] != did],
    )


@bp.get("/api/v1/projects/<pid>/datasets/<did>/values")
def field_values(pid, did):
    dataset = belongs(did, pid, "dataset")
    column = request.args.get("column", "")
    frame = data.load_frame(dataset)
    if dataset["data_type"] not in {"table", "metadata"} or column not in frame:
        raise data.DataError("这份文件中没有选择的列。")
    values = frame[column].astype(str).str.strip()
    counts = values[values.ne("")].value_counts()
    return {
        "items": [{"value": str(k), "count": int(v)} for k, v in counts.iloc[:200].items()],
        "total": len(counts),
    }


@bp.route("/api/v1/projects/<pid>/datasets/<did>/pathways", methods=["GET"])
def pathways(pid: str, did: str):
    q = request.args.get("q", "")
    d = belongs(did, pid, "dataset")
    if d["data_type"] != "functional":
        return []
    f = data.load_frame(d)
    names = list(dict.fromkeys((x.split("|", 1)[0] for x in f.index if not data.special(x))))
    return [x for x in names if q.lower() in x.lower()][:200]


@bp.route("/api/v1/templates/<kind>", methods=["GET"])
def template(kind: str):
    templates = {
        "metadata": "sample_id\tgroup\tsubject_id\nS01\tA\tP01\nS02\tB\tP02\n",
        "taxonomy": "feature_id\tS01\tS02\nSpecies_A\t0.6\t0.2\nSpecies_B\t0.4\t0.8\n",
        "functional": "# Pathway\tS01\tS02\nPWY-EXAMPLE: example pathway\t0.2\t0.3\n",
    }
    if kind not in templates:
        raise HTTPException(404)
    return Response(
        templates[kind],
        mimetype="text/tab-separated-values",
        headers={"Content-Disposition": f'attachment; filename="{kind}-template.tsv"'},
    )


@bp.route("/api/v1/skills", methods=["GET"])
def skill_catalog():
    return catalog()


@bp.route("/api/v1/projects/<pid>/chat", methods=["POST"])
def chat(pid: str):
    body = ChatRequest.model_validate(request.get_json(silent=True) or {})
    return agent.create_plan(pid, body)


@bp.route("/api/v1/projects/<pid>/plans/<plan_id>/clarify", methods=["POST"])
@created
def clarify_plan(pid: str, plan_id: str):
    belongs(plan_id, pid, "plan")
    body = ClarifyRequest.model_validate(request.get_json(silent=True) or {})
    return agent.clarify(pid, plan_id, body)


@bp.route("/api/v1/projects/<pid>/plans/<plan_id>/revise", methods=["POST"])
@created
def revise_plan(pid: str, plan_id: str):
    belongs(plan_id, pid, "plan")
    from .contracts import ReviseRequest

    body = ReviseRequest.model_validate(request.get_json(silent=True) or {})
    return agent.revise(pid, plan_id, body)


@bp.route("/api/v1/projects/<pid>/runs", methods=["POST"])
@created
def confirm_run_route(pid: str):
    return confirm_run(pid, RunConfirm.model_validate(request.get_json(silent=True) or {}))


def confirm_run(pid: str, body: RunConfirm):
    if db.get(pid, "project").get("archived"):
        raise HTTPException(409, "项目已删除，请先恢复项目。")
    plan = belongs(body.plan_id, pid, "plan")
    if plan.get("origin") == "model" and plan.get("planner_version") not in {
        "4.0",
        "5.0",
    }:
        raise HTTPException(409, "该方案由旧版规划器生成，请根据当前项目重新生成方案。")
    try:
        current_snapshot = agent.snapshot(pid, plan.get("dataset_ids"), plan.get("study_override"))
    except data.DataError:
        raise HTTPException(409, "方案所用文件已变化，请重新选择文件并生成方案。") from None
    if plan["fingerprint"] != agent.fingerprint(current_snapshot):
        raise HTTPException(409, "数据或样本设置已改变，请重新生成并确认方案。")
    parsed = AnalysisPlan.model_validate(
        {
            k: plan[k]
            for k in ("objective", "summary", "steps", "questions", "research_design")
            if k in plan
        }
    )
    selected = data.selected_inputs(pid, plan.get("dataset_ids"))
    agent.validate_plan(parsed, pid, selected)
    questions = agent.prerequisites(
        parsed, pid, selected, plan.get("study_override"), plan.get("accepted") or ()
    )
    if plan["questions"] or questions:
        raise data.DataError("；".join(list(dict.fromkeys(plan["questions"] + questions))))
    existing = next((r for r in db.list_records("run", pid) if r["plan_id"] == body.plan_id), None)
    if existing:
        return existing
    run = db.create(
        "run",
        pid,
        plan_id=body.plan_id,
        plan=parsed.model_dump(),
        snapshot=plan["snapshot"],
        status="queued",
        progress=0,
        current_step=0,
        results=[],
        cancel_requested=False,
        error=None,
    )
    db.create(
        "event",
        pid,
        run_id=run["id"],
        event_type="queued",
        message="方案已确认，等待执行。",
    )
    return run


@bp.route("/api/v1/projects/<pid>/runs/<rid>", methods=["GET"])
def run_detail(pid: str, rid: str):
    return belongs(rid, pid, "run")


@bp.route("/api/v1/projects/<pid>/runs/<rid>/cancel", methods=["POST"])
def cancel_run(pid: str, rid: str):
    r = belongs(rid, pid, "run")
    if r["status"] not in {"queued", "running"}:
        raise HTTPException(409, "此运行已经停止。")
    return db.update(
        rid,
        cancel_requested=True,
        **{"status": "cancelled"} if r["status"] == "queued" else {},
    )


@bp.route("/api/v1/projects/<pid>/runs/<rid>/retry", methods=["POST"])
def retry_run(pid: str, rid: str):
    if db.get(pid, "project").get("archived"):
        raise HTTPException(409, "项目已删除，请先恢复项目。")
    r = belongs(rid, pid, "run")
    if r["status"] not in {"failed", "interrupted", "cancelled"}:
        raise HTTPException(409, "运行状态不允许重试。")
    original_plan = db.get(r["plan_id"], "plan")
    if r["snapshot"] != agent.snapshot(
        pid, original_plan.get("dataset_ids"), original_plan.get("study_override")
    ):
        raise HTTPException(409, "数据版本已变化，请重新确认方案。")
    import hashlib

    successful = []
    by_step = {result["step_id"]: result for result in r.get("results", [])}
    retained = set()
    for step in r["plan"]["steps"]:
        result = by_step.get(step["id"])
        if (
            not result
            or result.get("status") != "success"
            or not set(step.get("depends_on", [])).issubset(retained)
        ):
            continue
        artifacts = result.get("artifacts", [])
        if not all(
            (runtime_root() / a["storage"]).resolve().is_relative_to(runtime_root())
            and (runtime_root() / a["storage"]).is_file()
            and hashlib.sha256((runtime_root() / a["storage"]).read_bytes()).hexdigest()
            == a["sha256"]
            for a in artifacts
        ):
            continue
        successful.append(result)
        retained.add(step["id"])
    return db.update(
        rid,
        status="queued",
        results=successful,
        failures=[],
        cancel_requested=False,
        error=None,
        partial=False,
    )


@bp.route("/api/v1/projects/<pid>/runs/<rid>/events", methods=["GET"])
def run_events(pid: str, rid: str):
    belongs(rid, pid, "run")
    return [e for e in db.list_records("event", pid) if e["run_id"] == rid]


@bp.route("/api/v1/projects/<pid>/runs/<rid>/logs/<step_id>", methods=["GET"])
def step_log(pid: str, rid: str, step_id: str):
    r = belongs(rid, pid, "run")
    if step_id not in [s["id"] for s in r["plan"]["steps"]]:
        raise HTTPException(404)
    path = runtime_root() / "projects" / pid / "runs" / rid / f"{step_id}.log"
    return {"text": path.read_text(errors="replace")[-15000:] if path.exists() else "暂无日志。"}


@bp.route("/api/v1/projects/<pid>/reports/<report_id>", methods=["GET"])
def get_report(pid: str, report_id: str):
    r = belongs(report_id, pid, "report")
    return {**r, "stale": report_stale(pid, r)}


def report_stale(pid, report):
    try:
        run = db.get(report["run_id"], "run")
        plan = db.get(run["plan_id"], "plan")
    except KeyError:
        return report["snapshot"] != agent.snapshot(pid)
    try:
        return report["snapshot"] != agent.snapshot(
            pid, plan.get("dataset_ids"), plan.get("study_override")
        )
    except data.DataError:
        return True


@bp.post("/api/v1/projects/<pid>/runs/<rid>/report")
def report_from_run(pid, rid):
    from .reports import create_report

    run = belongs(rid, pid, "run")
    if run["status"] != "completed":
        raise data.DataError("分析完成后才能整理报告。")
    return create_report(pid, rid, run["plan"]["objective"], run["results"], run["snapshot"])


@bp.route("/api/v1/projects/<pid>/reports/<report_id>", methods=["PUT"])
def edit_report(pid: str, report_id: str):
    body = ReportUpdate.model_validate(request.get_json(silent=True) or {})
    r = belongs(report_id, pid, "report")
    allowed_evidence = {e["id"] for e in r["evidence"]}
    if len({s.id for s in body.sections}) != len(body.sections):
        raise data.DataError("章节 ID 必须唯一。")
    for section in body.sections:
        if not set(section.evidence_ids).issubset(allowed_evidence):
            raise data.DataError("引用了未知证据。")
        for aid in section.artifact_ids:
            a = belongs(aid, pid, "artifact")
            run = db.get(r["run_id"], "run")
            allowed_artifacts = {
                x["id"] for result in run["results"] for x in result.get("artifacts", [])
            }
            if aid not in allowed_artifacts or a["artifact_type"] != "png":
                raise data.DataError("图表不属于报告关联分析。")
    with db.session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(db.Record, report_id)
        if row.data["revision"] != body.expected_revision:
            raise HTTPException(409, "报告已被更新，请刷新后重试。")
        row.data = {
            **row.data,
            "title": body.title,
            "sections": [s.model_dump() for s in body.sections],
            "revision": body.expected_revision + 1,
        }
        session.flush()
        return db.serialize(row)


@bp.route("/api/v1/projects/<pid>/reports/<report_id>/export/<kind>", methods=["GET"])
def download_report(pid: str, report_id: str, kind: str):
    belongs(report_id, pid, "report")
    if kind not in {"pdf", "docx", "zip"}:
        raise HTTPException(404)
    return FileResponse(export(report_id, kind), filename="yanji-report." + kind)


@bp.route("/api/v1/artifacts/<aid>", methods=["GET"])
def get_artifact(aid: str):
    a = db.get(aid, "artifact")
    path = artifact_path(aid, a["project_id"])
    media = {
        "svg": "image/svg+xml",
        "chart": "application/json",
        "table": "text/tab-separated-values",
        "png": "image/png",
    }
    return FileResponse(path, media_type=media.get(a["artifact_type"], "application/octet-stream"))


@bp.route("/api/v1/artifacts/<aid>/preview", methods=["GET"])
def preview_artifact(aid: str):
    a = db.get(aid, "artifact")
    path = artifact_path(aid, a["project_id"])
    if a["artifact_type"] != "table":
        raise HTTPException(422, "仅支持表格预览。")
    with path.open() as f:
        reader = csv.reader(f, delimiter="\t")
        columns = next(reader)
        rows = []
        for i, row in enumerate(reader):
            if i >= 100:
                break
            rows.append(row)
    return {"columns": columns, "rows": rows}


@bp.route("/api/v1/settings/model", methods=["GET"])
def model_settings():
    cfg = model_config()
    return {
        "model": cfg["model"],
        "base_url": cfg["base_url"],
        "configured": bool(cfg["api_key"]),
    }


@bp.get("/api/v1/settings/literature")
def literature_settings():
    from .writing_support import config

    cfg = config()
    return {
        "configured": bool(cfg["url"]),
        "kind": cfg["kind"],
        "url": cfg["url"],
        "knowledge_base": cfg["kb"],
    }


@bp.post("/api/v1/projects/<pid>/literature")
def find_literature(pid):
    from .writing_support import retrieve

    return retrieve(pid, LiteratureRequest.model_validate(request.get_json(silent=True) or {}))


@bp.get("/api/v1/projects/<pid>/literature")
def literature_history(pid):
    db.get(pid, "project")
    return db.list_records("retrieval", pid)


@bp.post("/api/v1/projects/<pid>/reports/<rid>/literature")
def attach_literature(pid, rid):
    from .writing_support import attach

    class AttachRequest(StrictModel):
        retrieval_id: str
        paper_id: str
        expected_revision: int

    body = AttachRequest.model_validate(request.get_json(silent=True) or {})
    return attach(
        belongs(rid, pid, "report"),
        belongs(body.retrieval_id, pid, "retrieval"),
        body.paper_id,
        body.expected_revision,
    )


@bp.post("/api/v1/projects/<pid>/reports/<rid>/draft")
def writing_draft(pid, rid):
    from .writing_support import draft

    return draft(
        belongs(rid, pid, "report"),
        WritingRequest.model_validate(request.get_json(silent=True) or {}),
    )


@bp.route("/api/v1/settings/model/test", methods=["POST"])
def model_test():
    output = agent.model_json(
        [{"role": "user", "content": 'Return only JSON: {"ok":true}'}], timeout=30
    )
    return {"ok": output.get("ok") is True, "model": model_config()["model"]}


@bp.route("/api/v1/examples", methods=["POST"])
@created
def create_example():
    from .examples import install_example

    return install_example()


@bp.post("/api/v1/projects/<pid>/datasets")
@created
def upload_dataset(pid):
    db.get(pid, "project")
    file = request.files.get("file")
    if file is None or Path(file.filename or "").suffix.lower() not in {
        ".tsv",
        ".csv",
        ".txt",
        ".xlsx",
        ".json",
    }:
        raise data.DataError("支持 CSV、TSV、XLSX 和记录型 JSON 文件。")
    options = ImportOptions.model_validate_json(request.form.get("options", "{}"))
    raw = file.stream.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "单文件超过 100 MB。")
    return data.import_bytes(pid, file.filename, raw, options)


@bp.post("/api/v1/projects/<pid>/import-preview")
def import_preview(pid):
    db.get(pid, "project")
    file = request.files.get("file")
    if file is None or Path(file.filename or "").suffix.lower() not in {
        ".xlsx",
        ".tsv",
        ".csv",
        ".txt",
        ".json",
    }:
        raise data.DataError("支持 CSV、TSV、XLSX 和记录型 JSON 文件。")
    raw = file.stream.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "单文件超过 100 MB。")
    options = ImportOptions.model_validate_json(request.form.get("options", "{}"))
    if Path(file.filename).suffix.lower() == ".xlsx":
        sheets = data.workbook_preview(raw)
    else:
        sheets = [{"name": "", "columns": [], "rows": 0}]
    try:
        preview_frame, info = data.inspect(
            raw, file.filename, options.model_copy(update={"kind": "table"})
        )
    except data.DataError as exc:
        return {
            "sheets": sheets,
            "inferred": "table",
            "columns": [],
            "rows": 0,
            "preview": None,
            "issue": str(exc),
        }
    if Path(file.filename).suffix.lower() != ".xlsx":
        sheets = [
            {
                "name": "",
                "columns": preview_frame.columns.tolist(),
                "rows": len(preview_frame),
            }
        ]
    try:
        _, inferred_info = data.inspect(
            raw, file.filename, options.model_copy(update={"kind": "auto"})
        )
        inferred = inferred_info["data_type"]
    except data.DataError:
        inferred = "table"
    return {
        "sheets": sheets,
        "inferred": inferred,
        "columns": preview_frame.columns.tolist(),
        "rows": len(preview_frame),
        "preview": {
            "columns": preview_frame.columns.tolist()[:20],
            "rows": preview_frame.iloc[:5, :20].values.tolist(),
        },
    }


@bp.get("/api/v1/projects/<pid>/runs/<rid>/stream")
def event_stream(pid, rid):
    belongs(rid, pid, "run")

    def events():
        sent = set()
        while True:
            for event in db.list_records("event", pid):
                if event["run_id"] == rid and event["id"] not in sent:
                    sent.add(event["id"])
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            if db.get(rid, "run")["status"] not in {"queued", "running"}:
                break
            time.sleep(1)

    return Response(events(), mimetype="text/event-stream")
