from __future__ import annotations

import hashlib
import json

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from .. import db
from ..config import FONT, runtime_root
from ..contracts import SkillParams
from ..data import Blocker, DataError, load_frame, special, taxonomy_rank

_RANK_MARKER = {
    "phylum": "|p__",
    "family": "|f__",
    "genus": "|g__",
    "species": "|s__",
}

PALETTES = {
    "forest": [
        "#0d7a69",
        "#49a695",
        "#a1c7a5",
        "#d8b46b",
        "#8c9eae",
        "#b07b8a",
        "#556b45",
        "#c98e63",
    ],
    "ocean": [
        "#277da8",
        "#43a4b8",
        "#85ccd1",
        "#7661a8",
        "#b5a8cc",
        "#e1bd72",
        "#596b8c",
        "#71a5a0",
    ],
    "earth": [
        "#8e654e",
        "#b78b61",
        "#dbbb86",
        "#76895c",
        "#a1b18b",
        "#b67f7d",
        "#74675d",
        "#c8a5a0",
    ],
}


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, np.ndarray):
        return clean_json(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.bool_):
        return bool(value)
    return value


class Context:
    def __init__(
        self,
        project_id,
        run_id,
        step_id,
        snapshot,
        parameters,
        completed=None,
        bindings=None,
    ):
        self.project_id, self.run_id, self.step_id = project_id, run_id, step_id
        self.snapshot = snapshot
        self.params = SkillParams.model_validate(parameters)
        self.directory = runtime_root() / "projects" / project_id / "runs" / run_id / step_id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.artifacts = []
        self.warnings = []
        from ..result_inputs import read_tables, resolve

        self.used_inputs = resolve(project_id, bindings or {}, completed or [])
        self.inputs = read_tables(self.used_inputs)
        self.datasets = [db.get(i, "dataset") for i in snapshot["datasets"].values()]
        self.datasets = [
            {**d, "understanding": snapshot.get("understanding", {}).get(d["id"], {})}
            for d in self.datasets
        ]
        from ..preparation import prepare

        self.prepared = prepare(self.datasets, self.params.preparation, snapshot.get("study", {}))
        if self.prepared is not None:
            self.snapshot = {**snapshot, "study": dict(snapshot.get("study", {}))}
            if self.prepared.group_column:
                self.snapshot["study"]["group_column"] = self.prepared.group_column
        if FONT.exists():
            font_manager.fontManager.addfont(str(FONT))
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(FONT)).get_name()
        plt.rcParams.update(
            {
                "axes.spines.top": False,
                "axes.spines.right": False,
                "axes.unicode_minus": False,
                "font.size": 10,
                "figure.dpi": 140,
                "axes.titlepad": 18,
                "savefig.facecolor": "white",
            }
        )

    def find(self, kind, stratified=False):
        if kind == "functional":
            key = "stratified_rows" if stratified else "total_pathways"
            found = next(
                (d for d in self.datasets if d["data_type"] == kind and d["summary"].get(key, 0)),
                None,
            )
        else:
            found = next((d for d in self.datasets if d["data_type"] == kind), None)
        if found is None:
            raise DataError(f"缺少可用的 {kind} 数据。")
        return found

    def _rank_frame(self, frame):
        """Select or roll up MetaPhlAn rows to the requested taxonomic rank."""
        ranks = [taxonomy_rank(x) for x in frame.index]
        selected = frame.loc[np.array(ranks) == self.params.rank]
        if not selected.empty:
            return selected
        if self.params.rank == "species":
            raise DataError("数据没有可用的 species 层级条目。")
        # Roll species (or the finest available) up to the requested ancestor.
        marker = _RANK_MARKER[self.params.rank]
        source = frame.loc[np.array(ranks) == "species"]
        if source.empty:
            source = frame
        labels = []
        keep = []
        for name in source.index:
            if marker not in name:
                continue
            labels.append(
                name.split(marker, 1)[0] + marker + name.split(marker, 1)[1].split("|", 1)[0]
            )
            keep.append(name)
        if not keep:
            raise DataError(f"数据没有可用的 {self.params.rank} 层级条目。")
        rolled = source.loc[keep].groupby(labels, sort=False).sum()
        self.warnings.append(
            f"输入没有独立的 {self.params.rank} 行；已将更细层级的行按该层级相加，结果只覆盖路径中出现该层级的特征。"
        )
        return rolled

    def matrix(self, kind="taxonomy", stratified=False):
        d = self.find(kind, stratified)
        f = load_frame(d)
        if kind == "taxonomy" and d["source"] == "metaphlan":
            f = self._rank_frame(f)
        elif kind == "taxonomy" and self.params.rank != "species":
            raise DataError(
                f"标准丰度矩阵没有分类路径，不能推断 {self.params.rank}；请选择 species（原始特征层级）。"
            )
        if kind == "functional":
            mask = ["|" in x if stratified else "|" not in x for x in f.index]
            f = f.loc[mask]
            f = f.loc[[not special(x) for x in f.index]]
        excluded = set(self.snapshot.get("study", {}).get("excluded_samples", []))
        left_out = [s for s in f.columns if s in excluded]
        if left_out:
            # A sample set aside while settling the plan stays visible in the result.
            note = f"按方案确认的设置，本次未纳入 {len(left_out)} 个样本：{'、'.join(left_out[:8])}{'等' if len(left_out) > 8 else ''}。"
            if note not in self.warnings:
                self.warnings.append(note)
        f = f.loc[:, [s for s in f.columns if s not in excluded]]
        if self.prepared is not None:
            f = f.loc[:, f.columns.isin(self.prepared.frame.index)]
        wanted = [s for s in self.params.samples if s]
        if wanted:
            missing = [s for s in wanted if s not in f.columns]
            if missing:
                raise Blocker(
                    f"{len(missing)} 个指定样本不在当前丰度表中：{'、'.join(missing[:8])}{'等' if len(missing) > 8 else ''}。",
                    "samples_unknown",
                    dataset_id=d["id"],
                    samples=missing,
                )
            f = f.loc[:, wanted]
            if (
                note
                := f"按方案只分析指定的 {len(wanted)} 个样本：{'、'.join(wanted[:8])}{'等' if len(wanted) > 8 else ''}。"
            ):
                if note not in self.warnings:
                    self.warnings.append(note)
        if f.empty:
            raise DataError("筛选后没有可用数据。")
        matrix = f.T.astype(float)
        if kind == "taxonomy":
            zero = matrix.sum(axis=1) <= 0
            if zero.any():
                self.warnings.append(
                    f"{int(zero.sum())} 个全零样本在数值分析中不可计算，已在数据体检中保留其身份。"
                )
            matrix = matrix.loc[~zero]
            if matrix.empty:
                raise DataError("所选层级所有样本均为零，无法计算。")
            matrix = matrix.div(matrix.sum(axis=1), axis=0)
        return matrix

    def groups(
        self,
        samples,
        required=False,
        allow_multi=False,
        min_per_group=3,
        inferential=True,
    ):
        study = self.snapshot.get("study", {})
        metadata = next((d for d in self.datasets if d["data_type"] == "metadata"), None)
        if not metadata or not study.get("group_column"):
            if required:
                raise DataError("请上传样本信息表，并在样本设置中选择分组列。")
            return None
        frame = (self.prepared.frame if self.prepared is not None else load_frame(metadata)).astype(
            str
        )
        col = study["group_column"]
        if col not in frame:
            raise DataError("所选分组列不存在，请重新选择。")
        missing = set(samples) - set(frame.index)
        if missing:
            if required:
                source = next(
                    (d for d in self.datasets if d["data_type"] in {"taxonomy", "functional"}),
                    None,
                )
                raise Blocker(
                    f"{len(missing)} 个样本缺少对应信息，请先确认样本映射。",
                    "samples_unmatched",
                    dataset_id=source["id"] if source else None,
                )
            self.warnings.append("样本信息未完整匹配，本次图表不使用分组。")
            return None
        groups = frame.loc[samples, col].str.strip()
        if groups.eq("").any():
            if required:
                raise DataError("分组列包含空值，请先修复。")
            return None
        if not required:
            return groups
        if inferential and not study.get("independent"):
            raise DataError("请确认本次比较为独立样本设计。配对或重复测量不在首版支持范围。")
        for column in frame.columns:
            if column.lower() in {
                "subject_id",
                "participant_id",
                "patient_id",
                "受试者编号",
            } or any(
                f["name"] == column and f.get("role") == "subject"
                for f in metadata.get("understanding", {}).get("fields", [])
            ):
                ids = frame.loc[samples, column].str.strip()
                if inferential and ids[ids.ne("")].duplicated().any():
                    raise DataError("检测到重复受试者标识，不能按独立样本进行检验。")
        names = groups.unique().tolist()
        chosen = [v for v in self.params.group_values if v]
        if not chosen and self.params.group_a and self.params.group_b:
            chosen = [self.params.group_a, self.params.group_b]
        if chosen:
            if len(set(chosen)) != len(chosen) or not set(chosen).issubset(names):
                raise DataError("指定的比较组无效或不完整。")
            groups = groups[groups.isin(chosen)]
        count = groups.nunique()
        if count < 2:
            raise DataError("组间分析至少需要两个组。")
        if count != 2 and not allow_multi:
            raise DataError("请选择两个比较组，或使用支持多组的分析。")
        if groups.value_counts().min() < min_per_group:
            raise Blocker(
                f"每个比较组至少需要 {min_per_group} 个有效样本；当前只适合描述性分析。",
                "groups_too_small",
            )
        return groups

    def artifact(self, path, kind, title, **extra):
        row = db.create(
            "artifact",
            self.project_id,
            run_id=self.run_id,
            step_id=self.step_id,
            filename=path.name,
            storage=str(path.relative_to(runtime_root())),
            artifact_type=kind,
            title=title,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            **extra,
        )
        self.artifacts.append(row)
        return row

    def table(self, frame, name, title):
        path = self.directory / f"{name}.tsv"
        frame.to_csv(
            path,
            sep="\t",
            index=True,
            index_label=frame.index.name or "ID",
            na_rep="NA",
            float_format="%.12g",
        )
        return self.artifact(path, "table", title, output_key=name)

    def chart(self, figure, name, title, option=None):
        for suffix in ("png", "svg"):
            path = self.directory / f"{name}.{suffix}"
            figure.savefig(path, bbox_inches="tight", dpi=180)
            self.artifact(path, suffix, title)
        plt.close(figure)
        if option:
            path = self.directory / f"{name}.chart.json"
            path.write_text(json.dumps(clean_json(option), ensure_ascii=False), encoding="utf-8")
            self.artifact(path, "chart", title)

    @property
    def colors(self):
        return PALETTES[self.params.palette]

    def evidence(self, key, statement, values, artifact_ids=None):
        return {
            "id": f"{self.step_id}:{key}",
            "statement": statement,
            "values": clean_json(values),
            "artifact_ids": artifact_ids
            or [a["id"] for a in self.artifacts if a["artifact_type"] == "table"],
            "dataset_ids": list(self.snapshot["datasets"].values()),
            "parameters": self.params.model_dump(),
            "upstream_inputs": self.used_inputs,
        }
