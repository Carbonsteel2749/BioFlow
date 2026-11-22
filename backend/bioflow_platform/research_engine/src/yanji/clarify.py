"""Everything that stops a plan is a question the user can answer on the plan.

A plan can be stopped by three parties: the planning model (a question about the
research), the distribution resolver (a research phrase that has no column yet) and
the program's own checks (a precondition of a step that the data does not meet).
All three end here as `Clarification` cards. Each card has an input and a place
where the answer is written: field meaning, a step parameter, a study setting, the
file selection, the sample mapping, or the planner's instructions. `finish` is the
only place where a plan's questions are set, and it derives them from the cards, so
a question without a way to answer it cannot be stored.
"""

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from .contracts import (
    AnalysisPlan,
    Choice,
    Clarification,
    FieldOption,
    SkillParams,
    Step,
)
from .data import DataError, load_frame, sample_key

_DISTRIBUTION = re.compile(r"分布|频数|频率|直方图|描述统计|的占比|的构成")
_NOT_DISTRIBUTION = re.compile(
    r"相关|关联|关系|回归|组间|差异|显著|菌群|物种|通路|多样性|丰度|群落"
)
_ORDER_CONCEPT = re.compile(r"程度|严重|等级|分级|分期|分型|级别")
_ORDER_VALUE = re.compile(r"轻|中|重|高|低|级|期|度|型")
_NOMINAL = re.compile(r"性别|sexe|sex|gender|组别|分组|group|地区|职业|是否|民族|婚姻", re.I)
_FILLER = {
    "一下",
    "数据",
    "这些",
    "字段",
    "变量",
    "指标",
    "情况",
    "如何",
    "怎么",
    "其中",
}


def is_distribution(text: str) -> bool:
    return bool(_DISTRIBUTION.search(text)) and not _NOT_DISTRIBUTION.search(text)


def resolves_distribution(text: str, datasets) -> bool:
    """Only requests this module can settle itself; the rest goes to the planner."""
    return (
        is_distribution(text)
        and bool(extract_concepts(text))
        and any(d["data_type"] in {"table", "metadata"} for d in datasets)
    )


def extract_concepts(text: str) -> list[str]:
    match = re.search(
        r"(.+?)(?:的)?(?:分布情况|分布|频数|频率|直方图|描述统计|占比|构成)",
        text,
    )
    if not match:
        return []
    span = match.group(1)
    span = re.sub(
        r"^.*(?:请|帮我|我想|希望|想要|分析|查看|了解|统计|描述|画出|绘制|比较|看看)",
        "",
        span,
    )
    span = re.sub(
        r"^(一下|下|这些数据里|这些数据中|这些|各|各个|不同|其中)",
        "",
        span,
    ).strip(" ，,。:：的")
    parts = [
        item.strip(" 「」\"'“”") for item in re.split(r"[、,，;；]|以及|和|与|及|\band\b", span)
    ]
    concepts = []
    for item in parts:
        if item and item not in _FILLER and len(item) <= 40 and item not in concepts:
            concepts.append(item)
    return concepts[:4]


def _norm(value: str) -> str:
    return re.sub(r"[\s_\-（）()\[\]【】]", "", str(value)).lower()


def _observed(series):
    text = series.astype(str).str.strip()
    text = text[~text.isin(["", "nan", "None", "NaN", "none"])]
    values = list(dict.fromkeys(text.tolist()))
    return values[:12], int(text.nunique())


def _numeric(info) -> bool:
    if info["role"] in {"categorical", "ordinal", "time", "identifier", "subject"}:
        return False
    if info["role"] == "numeric":
        return True
    return bool(info.get("numeric_candidate")) and info["role"] == "unknown"


def _profiles(datasets):
    from .understanding import describe

    rows = []
    for dataset in datasets:
        if dataset["data_type"] not in {"table", "metadata"}:
            continue
        frame = load_frame(dataset)
        described = {field["name"]: field for field in describe(dataset)["fields"]}
        for name in frame.columns:
            info = described.get(name)
            if not info or info["role"] in {"identifier", "subject"}:
                continue
            values, unique = _observed(frame[name])
            rows.append(
                {
                    **info,
                    "dataset_id": dataset["id"],
                    "filename": dataset["filename"],
                    "values": values,
                    "nunique": unique,
                }
            )
    return rows


def _score(concept: str, info) -> float:
    concept_key = _norm(concept)
    name = _norm(info["name"])
    meaning = _norm(info.get("meaning") or "")
    if not concept_key:
        return 0.0
    if concept_key == name or (meaning and concept_key == meaning):
        return 1.0
    best = 0.0
    if len(concept_key) >= 2 and (
        concept_key in name
        or name in concept_key
        or (meaning and (concept_key in meaning or meaning in concept_key))
    ):
        best = 0.86
    if _ORDER_CONCEPT.search(concept) and _ORDER_VALUE.search(" ".join(info["values"])):
        best = max(best, 0.76)
    return best


def _option(info) -> FieldOption:
    return FieldOption(
        dataset_id=info["dataset_id"],
        field=info["name"],
        values=info["values"],
    )


def _match(concept: str, profiles):
    ranked = sorted(
        ((_score(concept, info), info) for info in profiles),
        key=lambda item: (-item[0], item[1]["filename"], item[1]["name"]),
    )
    if not ranked or ranked[0][0] < 0.55:
        return None, False
    suggestion, score = ranked[0][1], ranked[0][0]
    locked = score >= 0.95 and (len(ranked) == 1 or ranked[1][0] < 0.95)
    return suggestion, locked


def _needs_encoding(concept: str, info) -> bool:
    if _numeric(info) or info.get("encoding"):
        return False
    if info["nunique"] > 12:
        return False
    if (
        _NOMINAL.search(concept)
        or _NOMINAL.search(info["name"])
        or _NOMINAL.search(info.get("meaning") or "")
    ):
        return False
    return info["role"] == "ordinal" or bool(_ORDER_CONCEPT.search(concept))


def _preview(values) -> str:
    shown = "、".join(values[:8])
    if len(values) > 8:
        shown += "等"
    return shown or "（没有非空取值）"


def enrich_distribution(plan: AnalysisPlan, message: str, datasets, include_report: bool):
    concepts = extract_concepts(message)
    profiles = _profiles(datasets)
    if not concepts:
        plan.summary = "这个问题像是在看分布，但没有识别出具体字段。"
        plan.steps = [Step(id="s1", skill="data_check", reason="确认所选表格可以读取。")]
        plan.clarifications = []
        plan.questions = ["请写明要查看分布的字段，用顿号分开，例如：年龄、性别、诊断。"]
        return plan
    if not profiles:
        plan.summary = "分布分析需要一张带字段的样本表或普通表格。"
        plan.steps = [Step(id="s1", skill="data_check", reason="确认所选表格可以读取。")]
        plan.clarifications = []
        plan.questions = ["当前选择里没有可读取的表格字段。请改选文件或上传包含这些字段的表。"]
        return plan

    cards = []
    resolved = []
    for concept in concepts:
        suggestion, locked = _match(concept, profiles)
        options = []
        if suggestion:
            options.append(_option(suggestion))
        for info in profiles:
            if suggestion and (
                info["dataset_id"] == suggestion["dataset_id"]
                and info["name"] == suggestion["name"]
            ):
                continue
            options.append(_option(info))
        options = options[:40]
        if not locked:
            where = (
                f"（{suggestion['filename']}）"
                if suggestion and len({p["dataset_id"] for p in profiles}) > 1
                else ""
            )
            hint = (
                f"建议对应「{suggestion['name']}」{where}，当前取值包括{_preview(suggestion['values'])}。"
                if suggestion
                else "请从当前表格中选择一列。"
            )
            cards.append(
                Clarification(
                    id=f"c{len(cards) + 1}",
                    kind="bind_field",
                    concept=concept,
                    prompt=f"「{concept}」没有可以直接使用的同名列。{hint}",
                    dataset_id=suggestion["dataset_id"] if suggestion else None,
                    field=suggestion["name"] if suggestion else None,
                    options=options,
                    values=suggestion["values"] if suggestion else [],
                )
            )
        else:
            resolved.append((suggestion["dataset_id"], suggestion["name"], suggestion))
        chosen = suggestion
        if chosen and _numeric(chosen) and not (chosen.get("unit") or "").strip():
            cards.append(
                Clarification(
                    id=f"c{len(cards) + 1}",
                    kind="unit",
                    concept=concept,
                    prompt=(
                        f"请确认「{chosen['name']}」的单位"
                        f"{'，它被用来表示「' + concept + '」' if concept != chosen['name'] else ''}。"
                        "不知道可以标明单位未知，图中仍按原始数值绘制。"
                    ),
                    dataset_id=chosen["dataset_id"],
                    field=chosen["name"],
                    values=chosen["values"],
                )
            )
        if chosen and _needs_encoding(concept, chosen):
            cards.append(
                Clarification(
                    id=f"c{len(cards) + 1}",
                    kind="encoding",
                    concept=concept,
                    prompt=(
                        f"请确认「{chosen['name']}」表示「{concept}」时的等级顺序。"
                        "按从高到低点选取值；没有固定顺序可以直接更新，图中按频数排列。"
                    ),
                    dataset_id=chosen["dataset_id"],
                    field=chosen["name"],
                    options=options,
                    values=chosen["values"],
                )
            )
        if len(cards) >= 8:
            break

    if cards:
        names = "、".join(concepts)
        plan.summary = (
            f"还不能直接计算{names}的分布。确认列的对应、单位或等级顺序后，会生成可执行方案。"
        )
        plan.steps = [Step(id="s1", skill="data_check", reason="等待字段说明完成后再计算分布。")]
        plan.clarifications = cards[:8]
        plan.questions = [card.prompt for card in plan.clarifications]
        return plan

    grouped = {}
    labels = []
    for dataset_id, column, _info in resolved:
        bucket = grouped.setdefault(dataset_id, [])
        if column not in bucket:
            bucket.append(column)
            labels.append(column)
    steps = [Step(id="s1", skill="data_check", reason="确认所选表格可以读取。")]
    for index, (dataset_id, columns) in enumerate(grouped.items(), start=2):
        steps.append(
            Step(
                id=f"s{index}",
                skill="field_distribution",
                reason=f"描述{'、'.join(columns)}的分布。",
                parameters=SkillParams(dataset_id=dataset_id, fields=columns),
                depends_on=["s1"],
            )
        )
    if include_report:
        steps.append(
            Step(
                id=f"s{len(steps) + 1}",
                skill="report",
                reason="把分布结果整理进报告。",
                depends_on=[],
            )
        )
    plan.steps = steps
    plan.clarifications = []
    plan.questions = []
    plan.summary = (
        f"将查看{'、'.join(labels)}的分布：数值字段给出直方图和描述统计，"
        "类别字段给出频数；已确认顺序的等级按该顺序排列。这是对当前表的描述。"
    )
    return plan


def _column(datasets, dataset_id, field):
    dataset = next(
        (
            item
            for item in datasets
            if item["id"] == dataset_id and item["data_type"] in {"table", "metadata"}
        ),
        None,
    )
    if dataset is None:
        raise DataError("请选择本次研究中的一张表格。")
    if field not in load_frame(dataset).columns:
        raise DataError(f"列「{field}」不在所选表格中。")
    return dataset


def _store(dataset, patches: dict[str, dict]):
    from .contracts import FieldMeaning, UnderstandingUpdate
    from .understanding import describe, save

    current = dataset.get("understanding") or {}
    profile = {field["name"]: field for field in describe(dataset)["fields"]}
    saved = {field["name"]: dict(field) for field in current.get("fields", [])}
    for name, patch in patches.items():
        if name not in profile:
            raise DataError(f"列「{name}」不在字段说明中。")
        previous = saved.get(name, {})
        base = {
            "name": name,
            "meaning": previous.get("meaning") or profile[name].get("meaning") or "",
            "unit": previous.get("unit") or "",
            "encoding": previous.get("encoding") or "",
            "role": previous.get("role")
            if name in saved
            else profile[name].get("role") or "unknown",
        }
        base.update(patch)
        saved[name] = base
    save(
        dataset,
        UnderstandingUpdate(
            description=current.get("description", ""),
            fields=[FieldMeaning.model_validate(item) for item in saved.values()],
            expected_revision=current.get("revision", 0),
        ),
    )


@dataclass
class Outcome:
    """What the answers changed, and whether the steps have to be designed again."""

    notes: list = dataclass_field(default_factory=list)
    replan: bool = False
    instructed: bool = False
    params: dict = dataclass_field(default_factory=dict)
    step_params: dict = dataclass_field(default_factory=dict)
    study: dict | None = None
    dataset_ids: list | None = None
    accepted: list = dataclass_field(default_factory=list)


def _unmatched(source, metadata, study):
    index = [str(item) for item in load_frame(metadata).index]
    known, used = set(index), set(source["samples"])
    excluded = set(study.get("excluded_samples") or [])
    missing = [s for s in source["samples"] if s not in excluded and s not in known]
    return missing, [item for item in index if item not in used]


def _ungrouped(source, metadata, study):
    frame = load_frame(metadata).astype(str)
    column = study.get("group_column")
    if column not in frame:
        return []
    excluded = set(study.get("excluded_samples") or [])
    samples = [s for s in source["samples"] if s not in excluded and s in frame.index]
    values = frame.loc[samples, column].str.strip()
    return values[values.eq("")].index.tolist()


def apply_answers(project_id, plan, answers):
    from . import db
    from .data import remap, selected_inputs
    from .understanding import describe

    cards = {card["id"]: card for card in plan.get("clarifications") or []}
    if not cards:
        raise DataError("当前方案没有待补充的问题。")
    got = [answer.id for answer in answers]
    if len(got) != len(set(got)) or set(got) != set(cards):
        raise DataError("请完成每一项补充后再更新方案。")
    datasets = selected_inputs(project_id, plan.get("dataset_ids"))
    dataset_ids = [dataset["id"] for dataset in datasets]
    base = plan.get("study_override")
    if base is None:
        base = db.get(project_id, "project").get("study") or {}
    study = {
        "group_column": base.get("group_column"),
        "independent": bool(base.get("independent")),
        "excluded_samples": list(base.get("excluded_samples") or []),
    }
    before = ([*dataset_ids], {**study, "excluded_samples": [*study["excluded_samples"]]})
    described = {
        dataset["id"]: {field["name"]: field for field in describe(dataset)["fields"]}
        for dataset in datasets
        if dataset["data_type"] in {"table", "metadata"}
    }
    order = {
        "bind_field": 0,
        "unit": 1,
        "encoding": 2,
        "choice": 3,
        "text": 3,
        "select": 4,
        "samples": 5,
    }
    out = Outcome()
    patches = {}
    notes = {}
    for answer in sorted(answers, key=lambda item: order[cards[item.id]["kind"]]):
        card = cards[answer.id]
        kind = card["kind"]
        if kind in {"choice", "text"}:
            text = answer.text.strip()
            if card.get("required") and not text:
                raise DataError("请先回答标为必填的问题，再更新方案。")
            notes[card["id"]] = {
                "question": card["prompt"],
                "answer": text or "（未回答）",
            }
            # Free text can only be acted on by designing the steps again.
            out.replan = True
            out.instructed = out.instructed or bool(text)
            continue
        if kind == "select":
            items = {item["value"]: item for item in card.get("items") or []}
            picked = list(dict.fromkeys(answer.picked))
            if not card.get("min_pick", 1) <= len(picked) <= card.get("max_pick", 1) or any(
                value not in items for value in picked
            ):
                raise DataError("请在给出的选项中完成选择，再更新方案。")
            target = card.get("target") or ""
            values = {}
            if target == "inputs":
                dropped = set(items) - {picked[0]}
                dataset_ids = [item for item in dataset_ids if item not in dropped]
            elif target == "study.group_column":
                study["group_column"] = picked[0]
            elif target == "study.independent":
                study["independent"] = picked[0] == "yes"
                # A paired or repeated design needs different steps, not a setting.
                out.replan = out.replan or picked[0] != "yes"
            elif target == "accept":
                if picked[0] == "yes":
                    out.accepted.append(card.get("code") or "")
            elif target == "replan_descriptive":
                if picked[0] == "yes":
                    out.replan = True
                    out.instructed = True
                    notes[card["id"]] = {
                        "question": card["prompt"],
                        "answer": "改为描述性组成对比，不做组间检验",
                    }
                    continue
                notes[card["id"]] = {
                    "question": card["prompt"],
                    "answer": "暂不改为描述性对比",
                }
                continue
            elif target == "param.groups":
                if len(picked) == 2:
                    values = {"group_a": picked[0], "group_b": picked[1]}
                else:
                    values = {"group_values": picked}
            elif target == "param.fields":
                values = {"fields": picked}
            elif target == "param.samples":
                values = {"samples": picked}
            elif target.startswith("param."):
                values = {target[6:]: picked[0]}
            else:
                raise DataError("这一项补充无法写回方案，请重新生成方案。")
            for step_id in card.get("steps") or []:
                out.step_params.setdefault(step_id, {}).update(values)
            if target in {
                "param.groups",
                "param.variable",
                "param.samples",
                "param.group_by",
            }:
                out.params.update(values)
            notes[card["id"]] = {
                "question": card["prompt"],
                "answer": "、".join(items[value].get("label") or value for value in picked),
            }
            continue
        if kind == "samples":
            source = next((d for d in datasets if d["id"] == card.get("dataset_id")), None)
            metadata = next((d for d in datasets if d["data_type"] == "metadata"), None)
            if source is None or metadata is None:
                raise DataError("样本对应所用的文件已变化，请重新生成方案。")
            if card.get("code") == "samples_ungrouped":
                dropped = _ungrouped(source, metadata, study)
                mapping = {}
            else:
                missing, free = _unmatched(source, metadata, study)
                mapping = {k: v for k, v in answer.mapping.items() if v}
                targets = list(mapping.values())
                if (
                    not set(mapping) <= set(missing)
                    or len(set(targets)) != len(targets)
                    or not set(targets) <= set(free)
                ):
                    raise DataError("每个样本只能对应样本信息里一条还没有被使用的记录。")
                if mapping:
                    # Sample information rows take the name used in the abundance table.
                    newer = remap(
                        metadata["id"],
                        {target: sample for sample, target in mapping.items()},
                        [],
                    )
                    dataset_ids = [
                        newer["id"] if item == metadata["id"] else item for item in dataset_ids
                    ]
                    datasets = selected_inputs(project_id, dataset_ids)
                dropped = [sample for sample in missing if sample not in mapping]
            study["excluded_samples"] = sorted({*study["excluded_samples"], *dropped})
            parts = [f"{sample} 对应 {target}" for sample, target in mapping.items()]
            if dropped:
                parts.append(f"本次不纳入 {len(dropped)} 个样本：{_preview(dropped)}")
            notes[card["id"]] = {
                "question": card["prompt"],
                "answer": "；".join(parts) or "没有需要处理的样本",
            }
            continue
        if card.get("source") != "check":
            out.replan = True
        dataset_id = answer.dataset_id or card.get("dataset_id")
        field = answer.field or card.get("field")
        concept = card.get("concept") or ""
        if kind == "bind_field" and not field:
            raise DataError(f"请选择哪一列表示「{concept or '该说法'}」。")
        if not dataset_id or not field:
            raise DataError("补充项缺少要写回的列。")
        dataset = _column(datasets, dataset_id, field)
        info = described.get(dataset_id, {}).get(field, {})
        slot = patches.setdefault(dataset["id"], {}).setdefault(field, {})
        if kind == "bind_field":
            kept = next(
                (
                    item.get("meaning")
                    for item in (dataset.get("understanding") or {}).get("fields", [])
                    if item["name"] == field
                ),
                "",
            )
            # A meaning the user already saved for this column is not overwritten.
            if concept and not kept:
                slot["meaning"] = concept
            if "role" not in slot:
                if _numeric(info):
                    slot["role"] = "numeric"
                elif info.get("role") in {"ordinal", "time", "categorical"}:
                    slot["role"] = info["role"]
                else:
                    slot["role"] = "categorical"
            notes[card["id"]] = {
                "question": card["prompt"],
                "answer": f"使用列「{field}」",
            }
        elif kind == "unit":
            unit = answer.unit.strip()
            if not unit and answer.unknown_unit:
                unit = "未说明"
            if not unit:
                raise DataError("请填写单位，或标明单位未知。")
            slot["unit"] = unit
            if _numeric(info) or info.get("role") == "numeric":
                slot["role"] = "numeric"
            notes[card["id"]] = {
                "question": card["prompt"],
                "answer": f"「{field}」的单位：{unit}",
            }
        else:
            text = answer.encoding.strip()
            if _numeric(info):
                # A numeric column needs no level order; leave its meaning untouched.
                if not slot:
                    patches[dataset["id"]].pop(field)
                notes[card["id"]] = {
                    "question": card["prompt"],
                    "answer": f"「{field}」是数值列，不需要等级顺序",
                }
                continue
            slot["encoding"] = text or "未指定顺序"
            if text:
                slot["role"] = "ordinal"
            elif slot.get("role") in {None, "unknown", "ordinal"}:
                slot["role"] = "categorical"
            notes[card["id"]] = {
                "question": card["prompt"],
                "answer": f"「{field}」的等级顺序：{text}"
                if text
                else f"「{field}」没有固定顺序，按类别处理",
            }
    for dataset_id, fields in patches.items():
        if fields:
            _store(db.get(dataset_id, "dataset"), fields)
    out.notes = [notes[card_id] for card_id in cards if card_id in notes]
    if dataset_ids != before[0] or plan.get("dataset_ids") is not None:
        out.dataset_ids = dataset_ids
    if study != before[1] or plan.get("study_override") is not None:
        out.study = study
    return out


def model_cards(raw, questions, datasets):
    """Make every planner question answerable.

    Structured items from the model are checked against the real columns; anything
    that does not resolve falls back to a free-text card, so no question is read-only.
    """
    profiles = _profiles(datasets)

    def find(name, dataset_id=None):
        if not isinstance(name, str) or not name.strip():
            return None
        matches = [info for info in profiles if info["name"] == name.strip()]
        preferred = [info for info in matches if info["dataset_id"] == dataset_id]
        return (preferred or matches or [None])[0]

    cards, seen = [], set()

    def add(kind, prompt, **extra):
        if prompt in seen or len(cards) >= 8:
            return
        seen.add(prompt)
        cards.append(Clarification(id=f"c{len(cards) + 1}", kind=kind, prompt=prompt, **extra))

    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or item.get("question") or "").strip()[:800]
        if not prompt:
            continue
        kind = item.get("kind")
        dataset_id = item.get("dataset_id")
        concept = str(item.get("concept") or "").strip()[:80]
        names = []
        for source in (item.get("choices"), item.get("options")):
            for option in source if isinstance(source, list) else []:
                if isinstance(option, dict):
                    option = option.get("field") or option.get("name") or option.get("label")
                text = str(option).strip()[:120] if option is not None else ""
                if text and text not in names:
                    names.append(text)
        columns = [find(name, dataset_id) for name in names]
        field = find(item.get("field"), dataset_id)
        if kind in {"bind_field", "choice"} and len(names) >= 2 and all(columns):
            suggested = field if field in columns else None
            add(
                "bind_field",
                prompt,
                concept=concept,
                dataset_id=suggested["dataset_id"] if suggested else None,
                field=suggested["name"] if suggested else None,
                options=[_option(info) for info in columns][:40],
                values=suggested["values"] if suggested else [],
            )
        elif kind == "unit" and field:
            add("unit", prompt, dataset_id=field["dataset_id"], field=field["name"])
        elif kind == "encoding" and field and field["values"] and not _numeric(field):
            add(
                "encoding",
                prompt,
                dataset_id=field["dataset_id"],
                field=field["name"],
                values=field["values"],
            )
        elif names:
            add("choice", prompt, choices=names[:12])
        else:
            add("text", prompt)
    if not cards:
        # Plain questions are used only when the model gave no structured ones,
        # so the same question is not asked twice in different words.
        for question in questions:
            question = str(question).strip()[:800]
            if question:
                add("text", question)
    return cards


_REDESIGN = "可以在下面说明希望怎么调整，方案会按你的说明重新设计。"
_PARAM_LABELS = {
    "x": "第一个指标",
    "y": "第二个指标",
    "source": "起点列",
    "target": "终点列",
    "weight": "权重列",
}


def _ask(issue, action=""):
    """The fallback every blocker has: tell the planner how to proceed instead."""
    return {
        "kind": "text",
        "prompt": f"{issue}{_REDESIGN}",
        "source": "check",
        "code": issue.code,
        "required": True,
        "action": action,
    }


def _pick(issue, prompt, target, items, **extra):
    return {
        "kind": "select",
        "prompt": prompt,
        "source": "check",
        "code": issue.code,
        "target": target,
        "steps": issue.data.get("steps") or [],
        "items": items[:200],
        "required": True,
        **extra,
    }


def _ordered(info) -> bool:
    return info["role"] == "ordinal" and (info.get("encoding") or "未指定顺序") != "未指定顺序"


def _column_choice(info):
    meaning = info.get("meaning") or ""
    return Choice(
        value=info["name"],
        label=f"{info['name']}（{meaning}）"
        if meaning and meaning != info["name"]
        else info["name"],
    ).model_dump()


def _resolution(issue, plan, datasets, study, profiles):
    code, data = issue.code, issue.data
    by_id = {dataset["id"]: dataset for dataset in datasets}
    metadata = next((d for d in datasets if d["data_type"] == "metadata"), None)
    steps = {step.id: step for step in plan.steps}
    first = steps.get((data.get("steps") or [None])[0])
    if code == "several_inputs":
        items = [
            Choice(value=item, label=by_id[item]["filename"]).model_dump()
            for item in data.get("ids", [])
            if item in by_id
        ]
        return [_pick(issue, str(issue), "inputs", items)] if len(items) > 1 else None
    if code == "table_missing":
        items = [
            Choice(value=d["id"], label=d["filename"]).model_dump()
            for d in datasets
            if d["data_type"] in {"table", "metadata"}
        ]
        if not items:
            return [_ask(issue, "data")]
        return [_pick(issue, f"{issue}请选择这一步使用的表。", "param.dataset_id", items)]
    if code == "file_missing":
        return [_ask(issue, "data")]
    if code == "fields_invalid":
        items = [
            _column_choice(info)
            for info in profiles
            if info["dataset_id"] == data.get("dataset_id")
        ]
        if not items:
            return None
        return [_pick(issue, str(issue), "param.fields", items, max_pick=8)]
    if code in {"columns_invalid", "not_continuous", "columns_unusable"} and first:
        columns = [info for info in profiles if info["dataset_id"] == data.get("dataset_id")]
        taken = {
            getattr(first.parameters, name)
            for name in _PARAM_LABELS
            if name not in data.get("params", [])
        }
        cards = []
        for name in data.get("params", []):
            usable = [
                info
                for info in columns
                if info["name"] not in taken
                and (
                    first.skill == "sankey"
                    and name != "weight"
                    or _numeric(info)
                    or (first.skill == "table_correlation" and _ordered(info))
                )
            ]
            if not usable:
                return None
            cards.append(
                _pick(
                    issue,
                    f"{issue}请选择{_PARAM_LABELS[name]}。",
                    f"param.{name}",
                    [_column_choice(info) for info in usable],
                )
            )
        return cards or None
    if code == "needs_order":
        info = next(
            (
                item
                for item in profiles
                if item["dataset_id"] == data.get("dataset_id")
                and item["name"] == data.get("field")
            ),
            None,
        )
        if not info or not 3 <= info["nunique"] <= 12:
            return None
        return [
            {
                "kind": "encoding",
                "prompt": f"{issue}请按顺序点选各等级。",
                "source": "check",
                "code": code,
                "dataset_id": info["dataset_id"],
                "field": info["name"],
                "values": info["values"],
            }
        ]
    if code in {"variable_missing", "variable_unusable"}:
        current = first.parameters.variable if first else None
        table_id = data.get("dataset_id") or (first.parameters.dataset_id if first else None)
        if not table_id and metadata:
            table_id = metadata["id"]
        usable = [
            info
            for info in profiles
            if (not table_id or info["dataset_id"] == table_id)
            and _numeric(info)
            and info["name"] != current
        ]
        # Association skills read the sample table; keep metadata columns if needed.
        if not usable and metadata:
            usable = [
                info
                for info in profiles
                if info["dataset_id"] == metadata["id"]
                and _numeric(info)
                and info["name"] != current
            ]
        if not usable:
            return [_ask(issue, "" if metadata or table_id else "data")]
        named = f"当前选择的是「{current}」。" if current else ""
        return [
            _pick(
                issue,
                f"{issue}{named}请选择要分析的数值指标。",
                "param.variable",
                [_column_choice(info) for info in usable],
            )
        ]
    if code in {"group_by_missing", "group_by_invalid"}:
        table_id = data.get("dataset_id") or (first.parameters.dataset_id if first else None)
        if not table_id and metadata:
            table_id = metadata["id"]
        usable = [
            info
            for info in profiles
            if (not table_id or info["dataset_id"] == table_id)
            and not _numeric(info)
            and info.get("role") not in {"identifier", "subject", "time"}
        ]
        if not usable:
            return [_ask(issue, "" if table_id else "data")]
        return [
            _pick(
                issue,
                f"{issue}请选择用于分组的分类字段。",
                "param.group_by",
                [_column_choice(info) for info in usable],
            )
        ]
    if code in {"samples_unmatched", "samples_ungrouped"}:
        source = by_id.get(data.get("dataset_id"))
        if source is None or metadata is None:
            return None
        card = {
            "kind": "samples",
            "source": "check",
            "code": code,
            "target": "samples",
            "dataset_id": source["id"],
        }
        if code == "samples_ungrouped":
            missing = _ungrouped(source, metadata, study)
            return [
                {
                    **card,
                    "prompt": f"{len(missing)} 个样本在「{study.get('group_column')}」列没有填写组别："
                    f"{_preview(missing)}。更新方案后，这些样本本次不纳入比较。",
                }
            ]
        missing, free = _unmatched(source, metadata, study)
        where = f"{source['filename']} 有 {len(missing)} 个样本在「{metadata['filename']}」里找不到对应的行：{_preview(missing)}。"
        if len(missing) > 40 or not free:
            return [
                {
                    **card,
                    "prompt": where
                    + "更新方案后，这些样本本次不纳入分析。如果它们其实有对应的记录，请到项目文件里核对样本编号。",
                    "action": "data",
                }
            ]
        lookup = {}
        for item in free:
            lookup.setdefault(sample_key(item), []).append(item)
        return [
            {
                **card,
                "prompt": where
                + "可以为它们指定样本信息里对应的记录；不指定的样本本次不纳入分析。",
                "items": [
                    Choice(
                        value=sample,
                        hint=lookup[sample_key(sample)][0]
                        if len(lookup.get(sample_key(sample), [])) == 1
                        else "",
                    ).model_dump()
                    for sample in missing
                ],
                "choices": free[:200],
            }
        ]
    if code == "group_column_missing":
        usable = [
            info
            for info in profiles
            if metadata
            and info["dataset_id"] == metadata["id"]
            and 2 <= info["nunique"] <= 12
            and not _numeric(info)
        ]
        if not usable:
            return [_ask(issue, "" if metadata else "data")]
        return [
            _pick(
                issue,
                f"{issue}请选择记录组别的列。",
                "study.group_column",
                [
                    Choice(
                        value=info["name"],
                        label=f"{info['name']}（{_preview(info['values'])}）",
                    ).model_dump()
                    for info in usable
                ],
            )
        ]
    if code == "independence_unconfirmed":
        return [
            _pick(
                issue,
                "这项比较按独立样本检验。同一人或同一研究对象，是否只记录了一次，并且不属于配对样本？",
                "study.independent",
                [
                    Choice(value="yes", label="是，每个研究对象只记录一次").model_dump(),
                    Choice(
                        value="no", label="不是，有配对或重复测量（暂不支持组间检验）"
                    ).model_dump(),
                ],
            )
        ]
    if code == "groups_unselected":
        items = [
            Choice(value=value, label=f"{value}（{count} 个样本）").model_dump()
            for value, count in (data.get("values") or {}).items()
        ]
        if len(items) < 2:
            return None
        return [
            _pick(
                issue,
                f"「{study.get('group_column')}」列里有 {len(items)} 个组别。"
                "请选择本次要比较的组别（至少两个；可选三组及以上）。",
                "param.groups",
                items,
                min_pick=2,
                max_pick=len(items),
            )
        ]
    if code == "groups_too_small":
        return [
            _pick(
                issue,
                f"{issue}是否改为对现有样本做描述性组成对比（共有/特有与丰度对照，不做显著性检验）？",
                "replan_descriptive",
                [
                    Choice(value="yes", label="改为描述性组成对比").model_dump(),
                    Choice(value="no", label="先不分析，我去调整样本或问题").model_dump(),
                ],
            )
        ]
    if code in {"samples_unknown", "samples_required"}:
        source = next(
            (d for d in datasets if d["data_type"] == "taxonomy"),
            next((d for d in datasets if d["data_type"] == "functional"), None),
        )
        if source is None:
            return [_ask(issue, "data")]
        items = [
            Choice(value=sample, label=sample).model_dump() for sample in source["samples"][:200]
        ]
        return [
            _pick(
                issue,
                f"{issue}请从丰度表中选择要分析的样本（可多选）。",
                "param.samples",
                items,
                min_pick=1 if code == "samples_required" else 1,
                max_pick=min(40, max(2, len(items))),
            )
        ]
    if code == "adjustment_unsupported":
        return [
            _pick(
                issue,
                "研究问题提到了协变量、混杂或回归，平台目前不能校正。是否按未校正的探索性分析继续？",
                "accept",
                [
                    Choice(value="yes", label="继续，结果按未校正的探索性分析解读").model_dump(),
                    Choice(value="no", label="先不继续，我来改写研究问题").model_dump(),
                ],
            )
        ]
    return None


def blocker_cards(issues, plan, datasets, study):
    profiles = _profiles(datasets)
    cards = []
    for issue in issues:
        cards.extend(_resolution(issue, plan, datasets, study, profiles) or [_ask(issue)])
    return cards


def finish(plan: AnalysisPlan, issues, datasets, study):
    """Set a plan's questions. Every question is a card, so every question can be answered."""
    cards = [card.model_dump() for card in plan.clarifications]
    asked = {card["prompt"] for card in cards}
    for question in plan.questions:
        question = str(question).strip()[:800]
        if question and question not in asked:
            asked.add(question)
            cards.append({"kind": "text", "prompt": question})
    for card in blocker_cards(issues, plan, datasets, study):
        if card["prompt"] not in asked:
            asked.add(card["prompt"])
            cards.append(card)
    plan.clarifications = [
        Clarification.model_validate({**card, "id": f"c{index}"})
        for index, card in enumerate(cards[:8], start=1)
    ]
    plan.questions = [card.prompt for card in plan.clarifications]
    return plan
