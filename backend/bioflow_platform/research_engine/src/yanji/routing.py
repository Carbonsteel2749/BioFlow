"""Map open research questions onto skills the platform can actually run."""

from __future__ import annotations

import re

from .contracts import AnalysisPlan, SkillParams, Step

_COMPARE = re.compile(r"差异|比较|对比|versus|\bvs\.?\b|不同|versus|显著|组间", re.I)
_CORE = re.compile(r"核心|共有|特有|检出率|common|core", re.I)
_PROFILE = re.compile(r"组成|构成|剖面|优势|分布|看看|查看|了解", re.I)
_PHYLUM = re.compile(r"门水平|门级别|phylum", re.I)
_FAMILY = re.compile(r"科水平|科级别|family", re.I)
_GENUS = re.compile(r"属水平|属级别|genus", re.I)
_MICROBIOME = re.compile(r"菌|物种|丰度|多样性|群落|通路|组成", re.I)
_SEX = re.compile(r"男女|男女性|性别|男生|女生|男性|女性")
_SIGNIFICANT = re.compile(r"显著|差异|组间|比较|是否不同|有没有差别|相关|关联|关系")
_BROADER = re.compile(
    r"通路|功能|相关|关联|回归|预测|热图|校正|协变量|评分|贡献|中介|分位|分组后",
    re.I,
)


def normalize_goal(text: str) -> str:
    return re.sub(r"[\t\u3000]+", " ", text or "").strip()


def match_samples(text: str, datasets) -> list[str]:
    """Return sample IDs that appear as whole tokens in the research question."""
    names = []
    for dataset in datasets:
        if dataset["data_type"] in {"taxonomy", "functional", "metadata"}:
            names.extend(dataset.get("samples") or [])
    # Longer IDs first so F2029-Z wins over F2029 when both exist.
    ordered = sorted(set(names), key=lambda item: (-len(item), item))
    found = []
    for name in ordered:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text):
            if name not in found:
                found.append(name)
    return found[:40]


def infer_rank(text: str) -> str:
    if _PHYLUM.search(text):
        return "phylum"
    if _FAMILY.search(text):
        return "family"
    if _GENUS.search(text):
        return "genus"
    return "species"


def resolve_named_samples(message: str, datasets, include_report: bool):
    """Build a plan when the user named concrete samples; None if not applicable."""
    text = normalize_goal(message)
    samples = match_samples(text, datasets)
    if not samples:
        return None
    if not any(d["data_type"] == "taxonomy" for d in datasets):
        return None
    # A named sample inside a larger study is not a request to plot only that sample.
    if _BROADER.search(text):
        return None
    rank = infer_rank(text)
    shown = "、".join(samples[:4])
    if _CORE.search(text):
        skill = "core_taxa"
        reason = f"按检出率查看 {shown} 相关的核心或共有特征。"
        params = SkillParams(samples=samples, rank=rank, prevalence=0.5)
    elif len(samples) >= 2 and _COMPARE.search(text):
        skill = "sample_comparison"
        reason = f"对 {shown} 做描述性菌种组成对比，给出丰度对照与共有/特有。"
        params = SkillParams(samples=samples, rank=rank)
    elif _PROFILE.search(text):
        skill = "sample_profile"
        reason = f"描述 {shown} 在 {rank} 层级的组成。"
        params = SkillParams(samples=samples, rank=rank)
    else:
        return None
    steps = [
        Step(id="s1", skill="data_check", reason="确认所选丰度表可以读取。"),
        Step(id="s2", skill=skill, reason=reason, parameters=params, depends_on=["s1"]),
    ]
    if include_report:
        steps.append(Step(id="s3", skill="report", reason="整理本次对比结果。"))
    scope = "、".join(samples[:6]) + (" 等" if len(samples) > 6 else "")
    return AnalysisPlan(
        objective=text,
        summary=(
            f"已识别目标中的样本 {scope}。"
            + (
                "每个样本只有一次观测，因此做描述性组成对比，不报告显著性。"
                if skill == "sample_comparison"
                else "按这些样本计算组成或核心特征，不扩大到全部队列。"
            )
        ),
        steps=steps,
    )


def _norm(value: str) -> str:
    return re.sub(r"[\s_\-（）()\[\]【】/]", "", str(value)).lower()


def _table_datasets(datasets):
    return [d for d in datasets if d["data_type"] in {"metadata", "table"}]


def _match_column(text: str, columns: list[str], aliases=None) -> str | None:
    """Pick the best column mentioned in the question or via aliases."""
    aliases = aliases or {}
    ordered = sorted(columns, key=lambda item: (-len(item), item))
    lowered = text.lower()
    for name in ordered:
        if name in text or name.lower() in lowered:
            return name
        compact = _norm(name)
        if len(compact) >= 2 and compact in _norm(text):
            return name
    for name in ordered:
        for alias in aliases.get(name, ()):
            if alias and (alias in text or _norm(alias) in _norm(text)):
                return name
    return None


def resolve_group_numeric(message: str, datasets, include_report: bool):
    """Route 'group vs numeric' questions onto group_numeric_compare when columns exist."""
    text = normalize_goal(message)
    if not _SIGNIFICANT.search(text) or _MICROBIOME.search(text):
        return None
    tables = _table_datasets(datasets)
    if not tables:
        return None
    from .data import load_frame
    from .understanding import describe

    candidates = []
    for dataset in tables:
        frame = load_frame(dataset)
        roles = {f["name"]: f for f in describe(dataset)["fields"]}
        categorical, numeric = [], []
        for column in frame.columns:
            info = roles.get(column, {})
            role = info.get("role")
            series = frame[column]
            if role in {"identifier", "subject", "time"}:
                continue
            if role == "categorical" or (
                role != "numeric"
                and series.astype(str).str.strip().nunique() <= 12
                and pd_to_numeric_ratio(series) < 0.8
            ):
                categorical.append(column)
            elif role in {"numeric", "ordinal"} or pd_to_numeric_ratio(series) >= 0.8:
                numeric.append(column)
        if not categorical or not numeric:
            continue
        aliases = {}
        for column in categorical + numeric:
            meaning = (roles.get(column) or {}).get("meaning") or ""
            aliases[column] = [meaning] if meaning and meaning != column else []
            compact = _norm(column)
            # "zn" is not a substring of "zinc"; match the element name explicitly.
            if (
                "锌" in column
                or "锌" in meaning
                or compact in {"zn", "zinc"}
                or compact.startswith("zn")
                or "zinc" in compact
            ):
                aliases.setdefault(column, []).extend(
                    ["zn", "Zn", "zinc", "Zinc", "锌", "锌含量", "zn含量", "ZN"]
                )
            if "性别" in column or "sex" in compact or "gender" in compact:
                aliases.setdefault(column, []).extend(["男女", "性别", "男女性"])
            if "年龄" in column or "age" in compact:
                aliases.setdefault(column, []).extend(["年龄", "age"])
        group_by = None
        if _SEX.search(text):
            group_by = _match_column(text, categorical, aliases) or next(
                (
                    c
                    for c in categorical
                    if "性别" in c or "sex" in _norm(c) or "gender" in _norm(c)
                ),
                None,
            )
        if group_by is None:
            group_by = _match_column(text, categorical, aliases)
        variable = _match_column(text, numeric, aliases)
        if group_by and variable and group_by != variable:
            candidates.append((dataset, group_by, variable))
    if not candidates:
        return None
    dataset, group_by, variable = candidates[0]
    params = SkillParams(
        dataset_id=dataset["id"],
        group_by=group_by,
        variable=variable,
    )
    steps = [
        Step(id="s1", skill="data_check", reason="确认所选表格与字段可以读取。"),
        Step(
            id="s2",
            skill="group_numeric_compare",
            reason=(
                f"按「{group_by}」分组，对「{variable}」做组间秩和检验，"
                "报告 p 值与各组中位数，直接回答是否存在组间差异。"
            ),
            parameters=params,
            depends_on=["s1"],
        ),
    ]
    if include_report:
        steps.append(Step(id="s3", skill="report", reason="整理组间比较结果与局限。"))
    return AnalysisPlan(
        objective=text,
        summary=(
            f"目标是比较「{variable}」在「{group_by}」各组间是否不同。"
            "将执行探索性 Mann–Whitney / Kruskal–Wallis，不做协变量校正；"
            "不会用字段分布或菌群检验替代。"
        ),
        steps=steps,
    )


def pd_to_numeric_ratio(series) -> float:
    import pandas as pd

    text = series.astype(str).str.strip()
    text = text[~text.isin(["", "nan", "None", "NaN", "none"])]
    if text.empty:
        return 0.0
    numeric = pd.to_numeric(text, errors="coerce")
    return float(numeric.notna().mean())


def assert_sample_scope(plan: AnalysisPlan, message: str, datasets) -> None:
    """Reject plans that name samples in the question but never put them in parameters."""
    samples = match_samples(normalize_goal(message), datasets)
    if len(samples) < 1:
        return
    scoped = {
        "sample_profile",
        "sample_comparison",
        "core_taxa",
        "composition",
        "alpha_diversity",
        "beta_diversity",
        "differential",
        "taxa_association",
        "diversity_association",
        "functional_association",
        "functional_overview",
        "functional_differential",
        "pathway_contribution",
        "community_comparison",
        "alpha_comparison",
    }
    needs = [s for s in plan.steps if s.skill in scoped]
    if not needs:
        return
    if any(s.parameters.samples for s in needs):
        return
    if len(samples) >= 2 and _COMPARE.search(message):
        raise ValueError(
            "目标点名了具体样本做对比，步骤必须在 parameters.samples 中列出这些样本，"
            "并使用 sample_comparison；不能用全体样本的 composition 代替。"
        )
    if len(samples) >= 1 and (_PROFILE.search(message) or _CORE.search(message)):
        raise ValueError(
            "目标点名了具体样本，步骤必须在 parameters.samples 中列出，不能分析全部样本却写这些样本名。"
        )


def assert_group_numeric_scope(plan: AnalysisPlan, message: str, datasets) -> None:
    """Reject distribution-only plans when the question asks for a group contrast."""
    text = normalize_goal(message)
    if not _SIGNIFICANT.search(text) or _MICROBIOME.search(text):
        return
    if not _table_datasets(datasets):
        return
    if any(s.skill in {"group_numeric_compare", "custom_analysis"} for s in plan.steps):
        return
    # Still collecting answers — do not force a skill yet.
    if plan.questions or plan.clarifications:
        return
    if any(s.skill == "table_correlation" for s in plan.steps):
        return
    # If the question is clearly a numeric-by-group contrast, do not accept
    # field_distribution / overview as the only substantive step.
    substantive = [s.skill for s in plan.steps if s.skill not in {"data_check", "report"}]
    if not substantive or set(substantive) <= {
        "field_distribution",
        "variable_distribution",
        "metadata_overview",
        "table_overview",
    }:
        raise ValueError(
            "目标要求按分类组比较数值指标是否不同，必须使用 group_numeric_compare，"
            "不能只用字段分布或概览代替显著性检验。"
        )
