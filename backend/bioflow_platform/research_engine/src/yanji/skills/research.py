"""Reusable clinical/microbiome analyses adapted from Jaliiina/biology.

No fixed filenames, symptom thresholds, candidate lists, or silent covariate drops.
"""

from types import SimpleNamespace

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

from ..data import Blocker, DataError, load_frame, special
from .context import Context

MISSING = {"", "NA", "NaN", "nan", "N/A", "/", "NULL", "None"}
STAT_SKILLS = {"baseline_table", "partial_correlation", "adjusted_regression"}
SKILLS = STAT_SKILLS | {
    "join_tables",
    "association_heatmap",
    "effect_plot",
    "association_network",
}


def cleaned(frame):
    return frame.copy().apply(
        lambda s: s.map(lambda v: np.nan if pd.isna(v) or str(v).strip() in MISSING else v)
    )


def dataset(ctx, identity=None, kinds=("metadata", "table")):
    candidates = [
        d for d in ctx.datasets if d["data_type"] in kinds and (not identity or d["id"] == identity)
    ]
    if len(candidates) != 1:
        raise DataError("请明确选择一份本次使用的文件，系统不会自行挑选或拼接同类表。")
    return candidates[0]


def metadata(ctx):
    if "data" in ctx.inputs:
        return None, cleaned(ctx.inputs["data"])
    identity = ctx.params.metadata_id
    if not identity and ctx.params.dataset_id:
        possible = [
            d
            for d in ctx.datasets
            if d["id"] == ctx.params.dataset_id and d["data_type"] in {"metadata", "table"}
        ]
        if possible:
            identity = possible[0]["id"]
    d = dataset(ctx, identity)
    f = (
        ctx.prepared.frame
        if ctx.prepared is not None and ctx.params.preparation[0].dataset_id == d["id"]
        else load_frame(d)
    )
    f = cleaned(f)
    excluded = ctx.snapshot.get("study", {}).get("excluded_samples", [])
    return d, f.loc[~f.index.isin(excluded)]


def frame_for(ctx, identity=None, fallback=None):
    if identity is None:
        return fallback, False
    d = dataset(ctx, identity, ("metadata", "table", "taxonomy", "functional"))
    if d["data_type"] in {"metadata", "table"}:
        f = (
            ctx.prepared.frame
            if ctx.prepared is not None and ctx.params.preparation[0].dataset_id == d["id"]
            else load_frame(d)
        )
        return cleaned(f), False
    f = load_frame(d)
    if d["data_type"] == "taxonomy":
        if d["source"] == "metaphlan":
            f = Context._rank_frame(ctx, f)
        elif ctx.params.rank != "species":
            raise DataError("该矩阵没有分类路径，不能推断其他分类层级。")
        matrix = f.T.astype(float)
        matrix = matrix.loc[matrix.sum(axis=1) > 0]
        matrix = matrix.div(matrix.sum(axis=1), axis=0)
    else:
        f = f.loc[[not special(str(k)) for k in f.index]]
        if ctx.params.functional_mode == "community":
            f = f.loc[["|" not in str(k) for k in f.index]]
            if f.empty:
                raise DataError(
                    "功能分析需要群落总通路行；分层贡献不能自动当作总通路。若明确做贡献合计代理，须显式选择该口径。"
                )
        elif ctx.params.functional_mode == "contribution_sum":
            f = f.loc[["|" in str(k) for k in f.index]]
            f = f.groupby([str(k).split("|", 1)[0] for k in f.index], sort=False).sum()
            ctx.warnings.append(
                "本次使用物种分层贡献合计代理；它不是HUMAnN重新计算的群落总通路，不能按群落总量解读。"
            )
        else:
            f = f.loc[["|" in str(k) for k in f.index]]
        matrix = f.T.astype(float)
    if matrix.empty:
        raise DataError("所选丰度层级或口径没有可用数据。")
    if ctx.prepared is not None:
        matrix = matrix.loc[matrix.index.isin(ctx.prepared.frame.index)]
    return matrix, True


def transformed(frame, method, pseudocount):
    f = frame.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    if method == "none":
        return f
    if (f.dropna() < 0).any().any():
        raise DataError("log1p/CLR只接受非负数，不能对负值静默变换。")
    if method == "log1p":
        return np.log1p(f)
    if f.shape[1] < 2:
        raise DataError("CLR至少需要两个成分，不能只对一个候选特征进行CLR。")
    log = np.log(f + pseudocount)
    complete = f.notna().all(axis=1) & f.sum(axis=1).gt(0)
    result = log.sub(log.mean(axis=1), axis=0)
    result.loc[~complete] = np.nan
    return result


def fields(frame, chosen, label, all_features=False):
    names = list(dict.fromkeys(chosen))
    if not names and all_features:
        names = list(frame.columns)
    if not names or any(n not in frame for n in names):
        raise DataError(f"请为{label}选择真实且存在的字段/特征。")
    return names


def analysis_frames(ctx):
    p = ctx.params
    d, meta = metadata(ctx)
    x, x_matrix = frame_for(ctx, p.dataset_id, meta)
    y, y_matrix = frame_for(ctx, p.outcome_dataset_id, meta)
    if not meta.index.is_unique or not x.index.is_unique or not y.index.is_unique:
        raise DataError("样本标识重复，不能进行逐样本关联。")
    identities = {v for v in [p.metadata_id, p.dataset_id, p.outcome_dataset_id] if v}
    if len(identities) > 1 and any(
        d["id"] in identities and d["data_type"] == "table" for d in ctx.datasets
    ):
        raise DataError("跨文件分析包含普通表，请先按明确编号合并，不能按自动行号对齐。")
    common = meta.index.intersection(x.index, sort=False).intersection(y.index, sort=False)
    excluded = set(ctx.snapshot.get("study", {}).get("excluded_samples", []))
    common = common[~common.isin(excluded)]
    if not len(common):
        raise DataError("所选输入没有共同样本，请先核对标识映射。")
    x, y, meta = x.loc[common], y.loc[common], meta.loc[common]
    # Transform the complete composition BEFORE selecting candidate taxa/pathways.
    x = transformed(x, p.transform, p.pseudocount)
    y = transformed(y, p.outcome_transform, p.pseudocount)
    predictors = fields(
        x,
        p.predictors or ([p.x or p.variable] if p.x or p.variable else []),
        "预测/暴露",
        x_matrix,
    )
    outcomes = fields(y, p.outcomes or ([p.y] if p.y else []), "结局", y_matrix)
    if "candidates" in ctx.inputs:
        selected = ctx.inputs["candidates"]
        if p.candidate_column not in selected and selected.index.name == p.candidate_column:
            selected = selected.reset_index()
        if p.candidate_column not in selected or "q" not in selected:
            raise DataError("候选结果须包含明确的特征列和q列。")
        q = pd.to_numeric(selected.q, errors="coerce")
        candidates = set(selected.loc[q.le(p.alpha), p.candidate_column].astype(str))
        predictors = [f for f in predictors if f in candidates]
        if not predictors:
            raise DataError("上游没有通过q阈值且能匹配本表的候选特征，未改用全部特征兜底。")
    if p.covariates and any(c not in meta for c in p.covariates):
        raise DataError("指定协变量不存在，不能静默跳过。")
    if set(p.covariates) & (set(predictors) | set(outcomes)):
        raise DataError("暴露或结局不能同时作为协变量控制自己。")
    if not set(p.categorical_covariates).issubset(p.covariates):
        raise DataError("分类协变量必须先列入本次协变量。")
    if len(predictors) * len(outcomes) > 50000:
        raise DataError("本次超过五万个模型，请用上游候选表或明确字段缩小范围。")
    if d:
        subjects = [
            f["name"]
            for f in d.get("understanding", {}).get("fields", [])
            if f.get("role") == "subject"
        ]
        subjects += [
            c
            for c in meta
            if c.lower() in {"subject_id", "participant_id", "patient_id", "受试者编号"}
        ]
        for c in set(subjects):
            values = meta[c].dropna().astype(str).str.strip()
            if values.duplicated().any():
                raise Blocker(
                    "存在重复受试者，本方法需要独立观测；不能按独立样本估计。",
                    "repeated_subjects",
                )
    return x[predictors], y[outcomes], meta, x_matrix, y_matrix


def design_covariates(meta, covariates, categorical=(), references=None, rank_numeric=False):
    columns = [pd.Series(1.0, index=meta.index, name="Intercept")]
    references = references or {}
    valid = pd.Series(True, index=meta.index)
    audit = {}
    for name in covariates:
        values = meta[name]
        numeric = pd.to_numeric(values, errors="coerce")
        is_category = name in categorical or (numeric.notna().sum() == 0 and values.notna().any())
        if is_category:
            levels = sorted(values.dropna().astype(str).unique())
            reference = references.get(name, levels[0] if levels else "")
            if reference not in levels:
                raise DataError(f"分类协变量「{name}」的参考水平不存在。")
            valid &= values.notna()
            for level in levels:
                if level != reference:
                    columns.append(
                        values.astype(str).eq(level).astype(float).rename(f"{name}={level}")
                    )
            audit[name] = {
                "kind": "categorical",
                "reference": reference,
                "levels": levels,
            }
        else:
            valid &= numeric.notna() & np.isfinite(numeric)
            if rank_numeric:
                numeric = numeric.rank(method="average")
            columns.append(numeric.rename(name))
            audit[name] = {"kind": "numeric", "ranked": rank_numeric}
    return pd.concat(columns, axis=1), valid, audit


def correction(table, params):
    table["q"] = np.nan
    table["correction_family"] = "all" if params.correction_family == "all" else table.outcome
    for _, indices in table.groupby("correction_family", sort=False).groups.items():
        valid = table.loc[indices, "p"].dropna()
        if params.adjustment == "BH" and len(valid):
            table.loc[valid.index, "q"] = stats.false_discovery_control(valid.values, method="bh")
    return table


def pair_model(x, y, meta, p, kind):
    valid = x.notna() & y.notna() & np.isfinite(x) & np.isfinite(y)
    numeric_covars = (
        meta[p.covariates].apply(lambda s: pd.to_numeric(s, errors="coerce"))
        if p.covariates
        else pd.DataFrame(index=meta.index)
    )
    # Complete observations first, then ranks: missing unrelated outcomes do not
    # change the ranks or cohort of the current pair.
    for covar in p.covariates:
        v = meta[covar]
        is_cat = covar in p.categorical_covariates or (
            numeric_covars[covar].notna().sum() == 0 and v.notna().any()
        )
        valid &= (
            v.notna()
            if is_cat
            else numeric_covars[covar].notna() & np.isfinite(numeric_covars[covar])
        )
    x, y, meta = x.loc[valid], y.loc[valid], meta.loc[valid]
    n = len(x)
    ranked = kind == "partial_correlation" and p.correlation_method == "spearman"
    controls, _, coding = design_covariates(
        meta, p.covariates, p.categorical_covariates, p.references, rank_numeric=ranked
    )
    k = controls.shape[1] - 1
    info = {"n": n, "excluded": len(valid) - n, "controls": k, "coding": coding}
    if n <= k + 3 or x.nunique() < 2 or y.nunique() < 2:
        return {
            **info,
            "status": "not_estimable",
            "reason": "有效样本不足或变量恒定",
            "effect": np.nan,
            "p": np.nan,
        }
    if np.linalg.matrix_rank(controls.to_numpy(dtype=float)) != controls.shape[1]:
        return {
            **info,
            "status": "not_estimable",
            "reason": "协变量共线或参考水平缺失",
            "effect": np.nan,
            "p": np.nan,
        }
    if kind == "partial_correlation":
        xv = x.rank(method="average").values if ranked else x.values
        yv = y.rank(method="average").values if ranked else y.values
        design = controls.to_numpy(dtype=float)
        rx = xv - design @ np.linalg.lstsq(design, xv, rcond=None)[0]
        ry = yv - design @ np.linalg.lstsq(design, yv, rcond=None)[0]
        if np.std(rx) < 1e-12 or np.std(ry) < 1e-12:
            return {
                **info,
                "status": "not_estimable",
                "reason": "校正后变量无剩余变异",
                "effect": np.nan,
                "p": np.nan,
            }
        if p.correlation_method == "residual_spearman":
            rho, value = stats.spearmanr(rx, ry)
            df = n - 2  # Legacy residual-then-Spearman, explicitly distinct from partial ranks.
        else:
            rho = float(np.corrcoef(rx, ry)[0, 1])
            df = n - k - 2
            value = (
                0.0
                if abs(rho) >= 1
                else float(2 * stats.t.sf(abs(rho) * np.sqrt(df / max(1e-30, 1 - rho**2)), df))
            )
        z = np.arctanh(np.clip(rho, -1 + 1e-15, 1 - 1e-15))
        delta = 1.96 / np.sqrt(n - k - 3)
        low, high = np.tanh([z - delta, z + delta])
        return {
            **info,
            "status": "estimated",
            "reason": "",
            "effect": rho,
            "p": float(value),
            "ci_low": low,
            "ci_high": high,
            "df": df,
            "metric": "residual_spearman_rho"
            if p.correlation_method == "residual_spearman"
            else "partial_spearman_rho"
            if ranked
            else "partial_pearson_r",
            "residual_x": rx,
            "residual_y": ry,
        }
    xv, yv = x.values.astype(float), y.values.astype(float)
    if p.standardize:
        xv = (xv - xv.mean()) / xv.std(ddof=1)
        yv = (yv - yv.mean()) / yv.std(ddof=1)
    design = controls.copy()
    design.insert(1, "Exposure", xv)
    if np.linalg.matrix_rank(design.values) != design.shape[1]:
        return {
            **info,
            "status": "not_estimable",
            "reason": "暴露与协变量共线",
            "effect": np.nan,
            "p": np.nan,
        }
    fit = (
        sm.OLS(yv, design).fit(cov_type="HC3", use_t=False)
        if p.standard_error == "HC3"
        else sm.OLS(yv, design).fit()
    )
    estimate, value = float(fit.params.Exposure), float(fit.pvalues.Exposure)
    ci = fit.conf_int().loc["Exposure"]
    if not np.isfinite([estimate, value, ci.iloc[0], ci.iloc[1]]).all():
        return {
            **info,
            "status": "not_estimable",
            "reason": "标准误或推断量不可估计",
            "effect": estimate,
            "p": np.nan,
        }
    return {
        **info,
        "status": "estimated",
        "reason": "",
        "effect": estimate,
        "p": value,
        "ci_low": ci.iloc[0],
        "ci_high": ci.iloc[1],
        "r_squared": float(fit.rsquared),
        "df": float(fit.df_resid),
        "metric": "standardized_beta" if p.standardize else "beta",
        "standard_error": p.standard_error,
    }


def associations(ctx, kind):
    from .analysis import result

    x, y, meta, _, _ = analysis_frames(ctx)
    records, details = [], []
    scatter = None
    for predictor in x:
        for outcome in y:
            model = pair_model(x[predictor], y[outcome], meta, ctx.params, kind)
            residual_x, residual_y = (
                model.pop("residual_x", None),
                model.pop("residual_y", None),
            )
            coding = model.pop("coding", {})
            records.append({"predictor": predictor, "outcome": outcome, **model})
            details.append(
                {
                    "predictor": predictor,
                    "outcome": outcome,
                    "n": model["n"],
                    "excluded": model["excluded"],
                    "covariate_coding": str(coding),
                    "status": model["status"],
                    "reason": model["reason"],
                }
            )
            if scatter is None and residual_x is not None:
                scatter = (predictor, outcome, residual_x, residual_y)
    table = correction(pd.DataFrame(records), ctx.params)
    ctx.table(table, "associations", "全部关联模型与多重检验结果")
    ctx.table(pd.DataFrame(details), "analysis_audit", "每个模型的样本排除与协变量编码")
    if not table.status.eq("estimated").any():
        raise DataError("全部模型均不可估计，已保留诊断表；未把本次分析标记为完成。")
    from .research_plots import heatmap_frame

    heatmap_frame(ctx, table, "association_heatmap", emit_table=False)
    if scatter is not None:
        predictor, outcome, rx, ry = scatter
        fig, ax = plt.subplots(figsize=(6.5, 4.5))
        ax.scatter(rx, ry, s=16, alpha=0.65, color=ctx.colors[0])
        ax.set(
            xlabel=f"{predictor} · 校正后残差",
            ylabel=f"{outcome} · 校正后残差",
            title="校正后的关联（该图不是原始值散点）",
        )
        ctx.chart(fig, "adjusted_scatter", "首个可估计关联的残差散点图")
    tested = int(table.status.eq("estimated").sum())
    qsignificant = int(table.q.le(ctx.params.alpha).sum())
    summary = f"请求 {len(table)} 个关联，实际估计 {tested} 个，{len(table) - tested} 个不可估计；BH q≤{ctx.params.alpha:g} 的结果 {qsignificant} 个。"
    if ctx.params.adjustment == "none":
        summary = f"请求 {len(table)} 个关联，实际估计 {tested} 个；未进行多重检验校正，不能把原始p值当作q值。"
    metrics = {
        "requested_models": len(table),
        "tested": tested,
        "not_estimable": len(table) - tested,
        "significant": qsignificant,
        "matched_samples": len(meta),
    }
    method = (
        "先对暴露、结局和数值协变量取平均秩，再控制协变量的偏Spearman；"
        if kind == "partial_correlation" and ctx.params.correlation_method == "spearman"
        else "先线性去除协变量再做残差Spearman（旧脚本口径，不等于秩先变换的偏Spearman）；"
        if kind == "partial_correlation" and ctx.params.correlation_method == "residual_spearman"
        else "Pearson偏相关；"
        if kind == "partial_correlation"
        else f"OLS；{ctx.params.standard_error}标准误；标准化={ctx.params.standardize}；"
    )
    method += f"完整观测；协变量={ctx.params.covariates}；多重校正={ctx.params.adjustment}，家族={ctx.params.correction_family}。"
    return result(ctx, kind, summary, method, metrics, [ctx.evidence("models", summary, metrics)])


def join_tables(ctx):
    from .analysis import result

    p = ctx.params
    left_d = dataset(ctx, p.dataset_id)
    right_d = dataset(ctx, p.right_dataset_id)
    if left_d["id"] == right_d["id"]:
        raise DataError("请选两份不同的表。")
    left, right = cleaned(load_frame(left_d)), cleaned(load_frame(right_d))

    def keyed(frame, d, key):
        if key:
            if key not in frame:
                raise DataError("指定连接字段不存在。")
            index = frame[key]
            if index.isna().any():
                raise DataError("连接标识存在空值，不能猜测对应关系。")
            frame = frame.drop(columns=[key]).copy()
            frame.index = index.astype(str).str.strip()
        elif d["data_type"] == "table":
            raise DataError("普通表请指定连接列，不能把自动行号当作样本标识。")
        if not frame.index.is_unique or (frame.index.astype(str).str.strip() == "").any():
            raise DataError("连接标识必须非空且唯一；暂不支持多对多连接。")
        return frame

    left, right = keyed(left, left_d, p.left_key), keyed(right, right_d, p.right_key)
    joined = left.join(right, how=p.join_how, rsuffix="_right", sort=False, validate="one_to_one")
    if joined.empty:
        raise DataError("两份表没有匹配记录。")
    audit = pd.DataFrame({"sample_id": left.index.union(right.index, sort=False)})
    audit["status"] = [
        "matched"
        if k in left.index and k in right.index
        else "left_only"
        if k in left.index
        else "right_only"
        for k in audit.sample_id
    ]
    ctx.table(joined, "joined", "按明确标识合并的数据")
    ctx.table(audit, "join_audit", "连接匹配与未匹配记录")
    summary = f"一对一{p.join_how}连接得到 {len(joined)} 条记录；匹配 {int(audit.status.eq('matched').sum())} 条。"
    return result(
        ctx,
        "join_tables",
        summary,
        "明确标识一对一连接，不自动合并重复标识，不填补缺失。",
        {"rows": len(joined), "matched": int(audit.status.eq("matched").sum())},
        [ctx.evidence("join", summary, {"rows": len(joined)})],
    )


def baseline_table(ctx):
    from .analysis import result

    _, frame = metadata(ctx)
    p = ctx.params
    if p.covariates:
        raise DataError("本工具生成未校正基线比较；协变量校正请使用回归或明确的自定义模型。")
    group = p.group_by or ctx.snapshot.get("study", {}).get("group_column")
    if not group or group not in frame:
        raise Blocker("请选择基线表的分组字段。", "group_by_missing")
    groups = frame[group].dropna().astype(str)
    chosen = p.group_values or (
        [p.group_a, p.group_b] if p.group_a and p.group_b else list(groups.unique())
    )
    if len(chosen) != 2 or len(set(chosen)) != 2 or not set(chosen).issubset(set(groups)):
        raise DataError("基线表需要明确的两个比较组。")
    names = fields(frame, p.fields, "基线表")
    records = []
    for name in names:
        values = frame[name]
        numeric = pd.to_numeric(values, errors="coerce")
        category = name in p.baseline_categorical or (
            numeric.notna().sum() == 0 and values.notna().any()
        )
        row = {
            "variable": name,
            "kind": "categorical" if category else "numeric",
            "p": np.nan,
            "test": "",
            "status": "estimated",
        }
        a, b = (
            values.loc[groups.index[groups.eq(chosen[0])]],
            values.loc[groups.index[groups.eq(chosen[1])]],
        )
        if category:
            levels = sorted(pd.concat([a, b]).dropna().astype(str).unique())
            counts = np.array(
                [
                    [a.dropna().astype(str).eq(level).sum() for level in levels],
                    [b.dropna().astype(str).eq(level).sum() for level in levels],
                ]
            )
            for i, (label, part) in enumerate(zip(chosen, [a, b])):
                row[f"n_{label}"] = int(part.notna().sum())
                row[f"missing_{label}"] = int(part.isna().sum())
                row[f"summary_{label}"] = "; ".join(
                    f"{level}: {counts[i, j]}/{part.notna().sum()}"
                    for j, level in enumerate(levels)
                )
            if len(levels) == 2 and counts.sum(axis=1).min() > 0:
                row.update(p=float(stats.fisher_exact(counts).pvalue), test="Fisher exact")
            elif len(levels) > 2 and (counts > 0).all():
                row.update(
                    p=float(stats.chi2_contingency(counts, correction=False).pvalue),
                    test="Chi-square",
                )
            else:
                row.update(
                    status="not_estimable",
                    reason="类别不足或存在稀疏/空单元格，未强行执行卡方",
                )
        else:
            parts = [
                pd.to_numeric(v, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
                for v in [a, b]
            ]
            for label, part, raw in zip(chosen, parts, [a, b]):
                row.update(
                    {
                        f"n_{label}": len(part),
                        f"missing_{label}": len(raw) - len(part),
                        f"mean_{label}": part.mean(),
                        f"sd_{label}": part.std(ddof=1),
                        f"median_{label}": part.median(),
                        f"q25_{label}": part.quantile(0.25),
                        f"q75_{label}": part.quantile(0.75),
                    }
                )
            if min(map(len, parts)) >= 3 and pd.concat(parts).nunique() > 1:
                value = (
                    stats.ttest_ind(*parts, equal_var=False).pvalue
                    if p.baseline_test == "welch"
                    else stats.mannwhitneyu(*parts, alternative="two-sided").pvalue
                )
                row.update(
                    p=float(value),
                    test="Welch t" if p.baseline_test == "welch" else "Mann–Whitney",
                )
            else:
                row.update(status="not_estimable", reason="样本不足或变量恒定")
        records.append(row)
    table = pd.DataFrame(records)
    table["q"] = np.nan
    valid = table.p.dropna()
    if p.adjustment == "BH" and len(valid):
        table.loc[valid.index, "q"] = stats.false_discovery_control(valid.values, method="bh")
    ctx.table(table, "baseline", "两组基线描述、分母和检验结果")
    summary = (
        f"比较 {chosen[0]} 与 {chosen[1]}，报告 {len(table)} 个字段；缺失不计为无症状或其他性别。"
    )
    return result(
        ctx,
        "baseline_table",
        summary,
        f"数值采用{p.baseline_test}，二分类采用Fisher；未校正协变量。多重校正={p.adjustment}。",
        {
            "variables": len(table),
            "not_estimable": int(table.status.ne("estimated").sum()),
        },
        [ctx.evidence("baseline", summary, {"groups": chosen})],
    )


def validate_parameters(step, datasets, study):
    """Schema/data readiness only; no statistical fitting before approval."""
    p = step.parameters
    if step.skill == "baseline_table" and p.covariates:
        raise DataError("本工具生成未校正基线比较；协变量校正请使用回归或自定义模型。")
    allowed = {
        "join_tables": set(),
        "baseline_table": {"data"},
        "partial_correlation": {"data", "candidates"},
        "adjusted_regression": {"data", "candidates"},
        "association_heatmap": {"data"},
        "effect_plot": {"data"},
        "association_network": {"left", "right"},
    }
    if set(step.inputs) - allowed[step.skill]:
        raise DataError("结果输入别名不适用于本步骤，请按该工具的数据输入约定连接。")
    if step.skill in {"association_heatmap", "effect_plot", "association_network"}:
        from .research_plots import result_frame, standard_result

        ctx = SimpleNamespace(params=p, datasets=datasets, inputs={}, warnings=[])
        aliases = ["left", "right"] if step.skill == "association_network" else ["data"]
        for alias in aliases:
            if alias in step.inputs:
                continue  # Actual rows are only available after approved execution.
            f = standard_result(
                ctx,
                result_frame(ctx, alias, right=alias == "right"),
                require_pair=step.skill != "effect_plot",
            )
            if (
                step.skill == "effect_plot"
                and p.effect_style == "forest"
                and (
                    p.effect_direction or not np.isfinite(f[["ci_low", "ci_high"]].to_numpy()).all()
                )
            ):
                raise DataError("森林图需要原始效应及真实置信区间；无区间可明确选择棒棒糖图。")
        return
    from ..preparation import prepare

    prepared = prepare(datasets, p.preparation, study)
    ctx = SimpleNamespace(
        params=p,
        datasets=datasets,
        snapshot={"study": study},
        prepared=prepared,
        inputs={},
        warnings=[],
    )
    if step.skill == "join_tables":
        dataset(ctx, p.dataset_id)
        dataset(ctx, p.right_dataset_id)
        return
    if "data" in step.inputs:
        if step.skill != "baseline_table" and (
            not (p.predictors or p.x or p.variable) or not (p.outcomes or p.y)
        ):
            raise DataError("使用上游合并表时，也需要明确暴露和结局字段。")
        return
    if step.skill == "baseline_table":
        _, f = metadata(ctx)
        fields(f, p.fields, "基线表")
        group = p.group_by or study.get("group_column")
        if not group or group not in f:
            raise Blocker("请选择用于基线表的分组字段。", "group_by_missing")
        return
    # Candidate rows are only available after the upstream step; validate all
    # references now, actual candidate membership immediately before fitting.
    analysis_frames(ctx)
