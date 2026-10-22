"""Deterministic, evidence-limited interpretation of validated result metrics."""
import math

FILTER_LABELS = {'low_quality_reads':'低质量', 'too_many_N_reads':'N 碱基过多', 'too_short_reads':'长度不足', 'too_long_reads':'长度过长', 'adapter_dimer_reads':'接头二聚体'}


def _finite(value, maximum=None, integer=False):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        if not math.isfinite(number) or number < 0 or (maximum is not None and number > maximum) or (integer and number != int(number)):
            return None
        return int(number) if integer else number
    except (ValueError, TypeError, OverflowError):
        return None


def qc_metrics(payload, raw, clean):
    summary = payload.get('summary', {})
    result = {'filter_reasons': {}}
    for label, source in [('before', 'before_filtering'), ('after', 'after_filtering')]:
        value = _finite(summary.get(source, {}).get('q30_rate'), maximum=1)
        result[f'q30_{label}_pct'] = value * 100 if value is not None else None
    if all(result[f'q30_{label}_pct'] is not None for label in ['before', 'after']):
        result['q30_change_pp'] = result['q30_after_pct'] - result['q30_before_pct']
    result['clean_bases'] = _finite(summary.get('after_filtering', {}).get('total_bases'), integer=True)
    filtering = payload.get('filtering_result', {})
    if isinstance(filtering, dict):
        reasons = {key: _finite(filtering[key], maximum=raw, integer=True) for key in FILTER_LABELS if key in filtering}
        if reasons and all(value is not None for value in reasons.values()) and sum(reasons.values()) == raw - clean:
            result['filter_reasons'] = reasons
    return result


def taxonomy_metrics(positive):
    values = [row['value'] for row in positive]
    total = math.fsum(values)
    probabilities = [value / total for value in values] if total else []
    shannon = -math.fsum(p * math.log(p) for p in probabilities) if probabilities else None
    return {**{f'top{n}_fraction_pct': math.fsum(values[:n]) * 100 for n in [3,5,10]},
            'species_table_fraction_sum': total,
            'shannon_observed': shannon,
            'pielou_observed': shannon / math.log(len(values)) if len(values) > 1 else None}


def kraken_metrics(content):
    counts = {}
    for line in content.splitlines():
        fields = line.split('\t')
        if len(fields) >= 6 and fields[3] in {'U','R'}:
            rank = fields[3]
            if rank in counts or fields[4] != ('0' if rank == 'U' else '1'):
                raise ValueError('duplicate or incompatible Kraken summary row')
            value = _finite(fields[1], integer=True)
            if value is None:
                raise ValueError('invalid Kraken count')
            counts[rank] = value
    if set(counts) != {'U','R'}:
        raise ValueError('missing Kraken summary rows')
    total = sum(counts.values())
    return {'kraken_total_units': total, 'kraken_classified_units': counts['R'],
            'kraken_classified_pct': counts['R'] / total * 100 if total else None}


def enrich_display(display, sections):
    for section in display:
        sample, category = section['sample_id'], section['category']
        rows = [s for s in sections if s['sample_id'] == sample and s['category'] == category]
        finding, interpretation, limitation, recommendation = [], [], [], []
        if category == 'qc':
            m = rows[0]['metrics']
            finding.append(f"质控保留 {m['clean_reads']:,} 条 reads，移除 {m['raw_reads']-m['clean_reads']:,} 条。")
            if m.get('q30_after_pct') is not None:
                finding.append(f"质控后 Q30 碱基占比为 {m['q30_after_pct']:.2f}%。")
            else:
                limitation.append('Q30 数据缺失或不合法，不能仅凭 reads 保留率评价碱基质量。')
            delta = m.get('q30_change_pp')
            if delta is not None:
                finding.append(f"相较质控前{'提高' if delta >= 0 else '降低'} {abs(delta):.2f} 个百分点。")
            if m.get('clean_bases') is not None:
                finding.append(f"质控后数据量为 {m['clean_bases'] / 1_000_000:.3f} Mb（双端碱基合计）。")
            reasons = sorted(m.get('filter_reasons', {}).items(), key=lambda item:item[1], reverse=True)
            if reasons and reasons[0][1] > 0:
                labels = '、'.join(f'{FILTER_LABELS[key]} {value:,} 条' for key,value in reasons if value)
                interpretation.append(f'过滤原因计数与总移除量一致：{labels}。')
            else:
                limitation.append('过滤原因未获得完整一致的计数，暂不判定主要损失来源。')
            if m.get('host_retained_pairs') is not None:
                finding.append(f"去宿主后保留 {m['host_retained_pairs']:,} 对 reads，作为后续分析的输入规模参考。")
            interpretation.append('Q30 描述碱基质量，保留率描述过滤损失，两者不能替代有效测序深度或注释充分性的评价。')
            limitation.append('这些指标不能证明已充分覆盖低丰度微生物或完整代谢通路，也不是样本合格与否的统一阈值。')
            recommendation.append('结合 FastQC/MultiQC 的逐碱基质量、接头残留及后续分类和注释结果，判断是否需要进一步检查样本或增加数据量。')
        elif category == 'taxonomy':
            m = rows[0]['metrics']; n = min(3, m['detected_taxa'])
            if n:
                finding.append(f"丰度最高的 {n} 个物种条目累计占比为 {m['top3_fraction_pct']:.2f}%，沿用来源物种表的丰度分母。")
                interpretation.append('这几个条目占本次物种级丰度估计的过半份额，组成主要由少数优势条目贡献。' if m['top3_fraction_pct'] > 50 else '最高丰度的几个条目未占过半份额，仍需结合其余物种条目观察整体组成。')
            else:
                finding.append('没有非零物种条目，无法评价组成集中程度。')
            if m.get('shannon_observed') is not None:
                finding.append(f"对结果表内条目重新归一化后，Shannon 指数为 {m['shannon_observed']:.3f}（自然对数）。")
            if m.get('pielou_observed') is not None:
                finding.append(f"Pielou 均匀度为 {m['pielou_observed']:.3f}。")
            if m.get('kraken_classified_pct') is not None:
                finding.append(f"Kraken2 将 {m['kraken_classified_units']:,}/{m['kraken_total_units']:,} 个分类单元归入参考分类体系，分类率为 {m['kraken_classified_pct']:.2f}%；双端模式下一个分类单元为一对 reads。")
                interpretation.append('Kraken2 分类率与 Bracken 物种丰度使用不同分母：物种表的比例不能直接解释为所有输入 reads 的占比。未分类部分属于当前参考数据库和参数下的识别缺口，不等同于污染。')
            else:
                limitation.append('未获得可校验的 Kraken2 分类率，暂不能评估未分类数据比例。')
            limitation.append('多样性指标仅描述当前表内检出的物种及其相对分布，受数据库、过滤阈值和测序深度影响；没有对照或参考范围，不判断高低、健康状态或菌群失调。')
            recommendation.append('核对分类率与优势物种的支持 reads；开展样本比较前统一数据库、处理参数及可比较的数据规模，并补充样本分组和潜在混杂因素。')
        else:
            by_kind = {s['function_type']:s['metrics'] for s in rows}
            if any(m.get('measurement', {}).get('status') != 'known' for m in by_kind.values()):
                limitation.append('部分功能结果单位未确认，原值仅用于同一来源表内的描述，不换算百分比或作跨样本定量比较。')
            labels = [('gene_family','基因家族'),('ko','KO'),('ec','EC'),('pathway_abundance','通路丰度'),('pathway_coverage','通路覆盖度')]
            finding.append('当前非零已命名条目数：' + '；'.join(f"{label} {by_kind[kind]['named_nonzero_count']}" for kind,label in labels if kind in by_kind) + '。')
            entries = any(by_kind.get(k,{}).get('named_nonzero_count',0)>0 for k in ['gene_family','ko','ec'])
            path = by_kind.get('pathway_abundance')
            if entries and path is not None and path['named_nonzero_count'] == 0:
                interpretation.append('已经获得基因家族或功能分组条目的注释，但未得到非零的已命名通路丰度结果；当前证据停留在条目层面，不能直接上升为通路层面的功能潜力结论。')
                recommendation.append('优先核查 HUMAnN 的比对与通路重建日志、有效数据规模及数据库 profile/映射文件是否匹配；这些是待排查方向，不是已确定的失败原因。')
            elif path is not None and path['named_nonzero_count'] > 0:
                interpretation.append('已获得通路层面的非零丰度估计，可以据已验证的通路名称描述群落功能潜力；仍需结合覆盖度及构成反应的证据复核，不代表通路完整或实际活跃。')
            else:
                interpretation.append('当前可用功能层次不足以形成完整的通路层面判断；缺失的表或缺少非零条目不能解释为相关功能不存在。')
            named = [f for row in rows for f in row['metrics']['top_features']]
            if named and not any(f['name'] and f['name'] != f['id'] for f in named):
                limitation.append('当前高丰度功能条目只有编号，缺少可校验的具体名称映射，不依据编号或模型记忆猜测功能。')
                recommendation.append('使用与当前注释数据库版本匹配的 KO/EC/UniRef 名称映射，核对具体反应或功能名称后再作机制讨论。')
            else:
                limitation.append('功能名称仅沿用结果表已有注释，不自动延伸为疾病相关性或机制已得到证实。')
            limitation.append('不同注释层面的数量不能相加，KO/EC 数量与基因家族数量之比也不能作为注释成功率；总体功能不能直接归属于优势物种。')
            if not recommendation:
                recommendation.append('优先复核主要通路及其构成基因证据；有重复样本和分组后，再设计经过标准化和多重检验校正的比较分析。')
        section['reasoning'] = {'finding': ''.join(finding), 'interpretation': ''.join(interpretation), 'limitation': ''.join(limitation), 'recommendation': ''.join(recommendation)}
        section['detail_text'] = section['text']
        section['text'] += '\n\n' + '\n\n'.join(f'{label}：{section["reasoning"][key]}' for key,label in [('finding','主要发现'),('interpretation','数据支持的解释'),('limitation','局限'),('recommendation','建议关注')])
    return display
