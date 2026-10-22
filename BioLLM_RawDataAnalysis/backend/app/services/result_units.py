"""Explicit table-unit contracts shared by preview and interpretation.

No inference from value magnitudes, filenames, or installed tool defaults.
This module describes display conversion only; source tables stay unchanged.
"""
import math


def measurement(rows, kind):
    units = {str(row.get('abundance_unit') or '').strip() for row in rows}
    methods = {str(row.get('normalization_method') or '').strip() for row in rows}
    unit = next(iter(units)) if len(units) == 1 else ''
    method = next(iter(methods)) if len(methods) == 1 else ''
    info = dict(status='unknown', source_unit=unit, display_unit='unknown',
                conversion_factor=1, normalization_method=method or '未记录',
                basis='source_table.abundance_unit', note='')
    if len(units) > 1 or len(methods) > 1:
        info.update(status='conflict', note='同表单位或标准化方法不一致，停止统一排序与定量解读；请核对下载表。')
        return info
    if kind == 'taxonomy' and unit in {'fraction_of_reads', 'percent_of_reads'}:
        factor = 100 if unit == 'fraction_of_reads' else 1
        info.update(status='known', display_unit='percent_of_reads', conversion_factor=factor)
        info['note'] = f'来源单位：{unit}；显示百分比 = 来源值 × {factor}，仅作显示换算，下载表原值不变。沿用来源表的丰度分母，不代表绝对菌量或全部输入 reads 的占比。'
    elif kind != 'taxonomy' and unit in {'RPK', 'CPM', 'relative_abundance', 'coverage'}:
        if (kind == 'pathway_coverage') != (unit == 'coverage'):
            info.update(status='conflict', note='单位与功能结果类型不兼容，停止定量解读；请核对下载表。')
            return info
        info.update(status='known', display_unit=unit)
        info['note'] = {
            'RPK': 'RPK（reads per kilobase）：已按序列长度校正，未按样本测序深度标准化；不作跨样本绝对水平比较。',
            'CPM': 'CPM（copies per million）：沿用来源声明的每百万拷贝尺度，不是原始 reads 计数或绝对丰度；跨样本比较仍需核对标准化分母、特殊条目处理及数据库版本。',
            'relative_abundance': '相对丰度（0–1）：沿用来源表的标准化分母，保留原值，不转换为 reads 百分比；不代表绝对功能量。',
            'coverage': '通路覆盖度（0–1）：保留原值，不作总和归一化，不等同于通路丰度、完整性或活性。',
        }[unit]
    else:
        info['note'] = f'单位未确认（来源标记：{unit or "未记录"}），保留原值，不转换为百分比。不能确认是否经过测序深度标准化，不作跨样本定量比较。'
    if kind != 'taxonomy':
        info['note'] += f' 来源标准化方法：{info["normalization_method"]}；本次未重新标准化，下载表原值不变。'
    return info


def display_value(value, info):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('invalid abundance')
    maximum = {'fraction_of_reads': 1, 'percent_of_reads': 100,
               'relative_abundance': 1, 'coverage': 1}.get(info['source_unit'])
    if maximum is not None and value > maximum:
        raise ValueError('abundance outside declared unit range')
    return value * info['conversion_factor']
