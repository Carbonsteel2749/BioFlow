"""Evidence-linked descriptive metagenomics summaries; no LLM inference."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from ..models import utc_now
from .previews import _safe_files, _host_metrics
from .interpretation_reasoning import qc_metrics, taxonomy_metrics, kraken_metrics, enrich_display
from .interpretation_ai import InterpretationAI, build_prompt
from .result_units import measurement, display_value

SPECIAL = re.compile(r'^(UNMAPPED|READS_UNMAPPED|UNGROUPED|UNINTEGRATED|UniRef\d+_unknown)(:|$)')
FUNCTIONS = {
    'read_genefamilies.tsv': ('基因家族', 'gene_family', 'UniRef90'),
    'read_ko.tsv': ('KO', 'ko', 'KO'),
    'read_ec.tsv': ('EC', 'ec', 'EC'),
    'read_pathabundance.tsv': ('通路丰度', 'pathway_abundance', 'MetaCyc'),
    'read_pathcoverage.tsv': ('通路覆盖度', 'pathway_coverage', 'MetaCyc'),
}


def _number(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError('数值不是有限的非负数')
    return number


def _display_sections(sections):
    """Group presentation only; preserve per-table metrics for API consumers."""
    display, groups = [], {}
    order = [('gene_family', '基因家族'), ('ko', 'KO'), ('ec', 'EC'),
             ('pathway_abundance', '通路丰度'), ('pathway_coverage', '通路覆盖度')]
    for section in sections:
        if section['category'] != 'functional':
            display.append(dict(section))
            continue
        sample = section['sample_id']
        if sample not in groups:
            groups[sample] = dict(category='functional', sample_id=sample, source_ids=[], paragraphs=[])
            display.append(groups[sample])
    for sample, group in groups.items():
        rows = {s['function_type']: s for s in sections if s['category'] == 'functional' and s['sample_id'] == sample}
        pathways, diagnostics, unit_notes = [], [], {}
        for kind, label in order:
            if kind not in rows:
                continue
            row = rows[kind]; metrics = row['metrics']
            text = f"结果中有 {metrics['named_nonzero_count']} 个非零的已命名条目。"
            if metrics['top_features']:
                leaders = []
                for feature in metrics['top_features']:
                    name = feature['id'] if feature['name'] == feature['id'] else f"{feature['id']}（{feature['name']}）"
                    leaders.append(f"{name}：{feature['value']:.4g}")
                text += '数值居前的条目为 ' + '、'.join(leaders) + '。'
            else:
                text += '当前没有可供生物学解释的非零已命名条目，不能据此认定相应功能不存在。'
            if kind.startswith('pathway_'):
                pathways.append(f'{label}：{text}')
            else:
                group['paragraphs'].append(dict(title=label, text=text))
            unit_notes.setdefault(metrics['measurement']['note'], []).append(label)
            if metrics['special_entries']:
                diagnostics.append(label + '：' + '、'.join(f'{key}={value:.4g}' for key, value in metrics['special_entries'].items()))
            for source_id in row['source_ids']:
                if source_id not in group['source_ids']:
                    group['source_ids'].append(source_id)
        if pathways:
            group['paragraphs'].append(dict(title='通路丰度与覆盖度', text='\n'.join(pathways)))
        notes = '已从上述条目统计与排序中排除 UNMAPPED、UNGROUPED、UNINTEGRATED 等诊断类别。'
        notes += ' '.join('、'.join(labels) + '：' + note for note, labels in unit_notes.items())
        notes += '各注释层面的条目存在对应或重叠，不相加为总功能数量。'
        group['common_notes'] = notes
        group['diagnostics'] = ('；'.join(diagnostics) + '。诊断值不是已命名功能，不能直接相加解释为未注释 reads 比例。') if diagnostics else ''
        group['text'] = '\n\n'.join([f'功能潜力 · {sample}'] + [f"{p['title']}\n{p['text']}" for p in group['paragraphs']] + ['单位与解释边界：' + notes] + (['诊断类别（分来源）：' + group['diagnostics']] if diagnostics else []))
    return display


class InterpretationService:
    def __init__(self, settings, repository):
        self.settings, self.repository = settings, repository
        self.ai = InterpretationAI(settings.ollama_url, settings.ollama_model, settings.ollama_timeout_seconds)

    def generate(self, task_id):
        task = self.repository.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        root = self.settings.state_root / 'outputs' / task_id
        sections, sources, warnings = [], [], []
        covered = {'fastp': set(), 'taxonomy': set(), 'functional_annotation': set()}
        succeeded = {s['name'] for s in task['steps'] if s['status'] == 'succeeded'}
        samples = set()
        try:
            with Path(task['manifest_path']).open(encoding='utf-8-sig') as handle:
                samples = {r['sample_id'] for r in csv.DictReader(handle)}
        except (OSError, KeyError, UnicodeError):
            warnings.append('样本清单不可读，不能确认全部样本的覆盖情况。')

        def snapshot(path):
            if path.resolve() != path or not path.is_relative_to(root) or path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError('文件不安全或超过读取上限')
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            source = {'id': hashlib.sha256(str(path.relative_to(root)).encode()).hexdigest()[:16],
                      'path': str(path.relative_to(root)), 'sha256': digest}
            return content.decode('utf-8-sig'), source

        def add(category, sample, text, metrics, evidence):
            sections.append(dict(category=category, sample_id=sample, text=text, metrics=metrics,
                                 source_ids=[s['id'] for s in evidence]))
            for source in evidence:
                if source not in sources:
                    sources.append(source)

        host = _host_metrics(root) if 'host_depletion' in succeeded else {}
        if 'fastp' in succeeded:
            for path in _safe_files(root / 'fastp', lambda p: p.name.endswith('.fastp.json')):
                sample = path.name.removesuffix('.fastp.json')
                try:
                    if samples and sample not in samples:
                        raise ValueError('质控样本不在本任务清单中')
                    content, source = snapshot(path)
                    payload = json.loads(content)
                    data = payload['summary']
                    raw = _number(data['before_filtering']['total_reads'])
                    clean = _number(data['after_filtering']['total_reads'])
                    if raw != int(raw) or clean != int(clean) or clean > raw:
                        raise ValueError('reads 计数不一致')
                    retention = clean / raw * 100 if raw else None
                    text = f'{sample}：fastp 记录质控前 {int(raw):,} 条 reads、质控后 {int(clean):,} 条 reads（双端合计，非 read pairs）。'
                    text += f'质控保留率为 {retention:.2f}%。' if retention is not None else '输入为零，保留率不可定义。'
                    evidence = [source]
                    metrics = dict(raw_reads=int(raw), clean_reads=int(clean), retention_pct=retention)
                    metrics.update(qc_metrics(payload, raw, clean))
                    if sample in host:
                        h = host[sample]
                        candidates = _safe_files(root / 'host_depletion', lambda p: p.name == f'{sample}.host_depletion.metrics.json')
                        if len(candidates) != 1:
                            raise ValueError('宿主统计来源不唯一')
                        _, hs = snapshot(candidates[0]); evidence.append(hs)
                        metrics.update(host_input_pairs=h['input_pair_count'], host_retained_pairs=h['retained_pair_count'], host_removed_pct=h['removed_pct'])
                        text += f"去宿主过滤输入 {h['input_pair_count']:,} 对、保留 {h['retained_pair_count']:,} 对 reads。"
                        text += f"按输入 read pairs 计算的移除比例为 {h['removed_pct']:.2f}%。" if h['removed_pct'] is not None else '去宿主过滤输入为零，移除比例不可定义。'
                        text += '该比例反映当前过滤策略下被移除的 read pairs，不等同于样本真实宿主细胞含量。'
                    else:
                        text += '去宿主统计未获得或该阶段未完成，不能解释为移除率为零。'
                    add('qc', sample, text, metrics, evidence)
                    covered['fastp'].add((sample, f'{sample}.fastp.json'))
                except (ValueError, KeyError, TypeError, OSError):
                    warnings.append(f'{sample}：质控统计缺失或不一致，未生成该部分结论。')

        for stage, names in [('taxonomy', {'species_abundance.tsv'}), ('functional_annotation', set(FUNCTIONS))]:
            if stage not in succeeded:
                warnings.append(f'{stage} 阶段未确认成功，本摘要不采用该阶段文件。')
                continue
            for path in _safe_files(root / stage, lambda p: p.name in names):
                try:
                    content, source = snapshot(path)
                    reader = csv.DictReader(io.StringIO(content), delimiter='\t')
                    rows = []
                    for index, row in enumerate(reader):
                        if index >= 200000:
                            raise ValueError('超出完整统计上限，拒绝以截断数据生成结论')
                        row['value'] = _number(row['abundance'])
                        rows.append(row)
                    if not rows:
                        warnings.append(f'{path.relative_to(root)}：结果表为空，不能解释为不存在相应生物学功能。')
                        continue
                    sample_ids = {r['sample_id'] for r in rows}
                    if len(sample_ids) != 1 or (samples and not sample_ids <= samples):
                        raise ValueError('样本范围不一致')
                    sample = next(iter(sample_ids))
                    if stage == 'taxonomy':
                        unit_info = measurement(rows, 'taxonomy')
                        if unit_info['status'] != 'known' or any(r['taxonomy_rank'] != 'species' for r in rows):
                            raise ValueError('分类层级或丰度单位不兼容')
                        for row in rows:
                            row['value'] = display_value(row['value'], unit_info) / 100
                        if len({r['taxid'] for r in rows}) != len(rows):
                            raise ValueError('重复物种行')
                        if math.fsum(r['value'] for r in rows) > 1.001:
                            raise ValueError('物种相对丰度总和超出允许的舍入误差')
                        positive = sorted((r for r in rows if r['value'] > 0), key=lambda r: r['value'], reverse=True)
                        top = [{'name': r['taxonomy'], 'fraction': r['value']} for r in positive[:3]]
                        leaders = '、'.join(f"{r['name']}（{r['fraction']*100:.2f}%）" for r in top)
                        text = f'{sample}：在当前参考数据库与分类参数下，物种级结果表包含 {len(positive)} 个非零丰度分类条目。'
                        text += f'相对丰度居前的条目为 {leaders}。' if leaders else '未获得非零物种条目；这不等同于样本不存在微生物。'
                        text += unit_info['note'] + '检出条目数不等于样本真实物种丰富度。'
                        metrics = dict(detected_taxa=len(positive), top_taxa=top, unit='fraction_of_reads', measurement=unit_info)
                        metrics.update(taxonomy_metrics(positive))
                        evidence = [source]
                        try:
                            report, report_source = snapshot(path.parent / 'kraken.report')
                            classified = kraken_metrics(report)
                            if sample in host and classified['kraken_total_units'] != host[sample]['retained_pair_count']:
                                raise ValueError('Kraken input count disagrees with retained pairs')
                            metrics.update(classified)
                            evidence.append(report_source)
                        except (OSError, ValueError, TypeError):
                            warnings.append(f'{sample}：Kraken2 分类率来源缺失或不一致，未生成分类率判断。')
                        add('taxonomy', sample, text, metrics, evidence)
                    else:
                        label, kind, namespace = FUNCTIONS[path.name]
                        if any(r['function_source'] != 'HUMAnN' or r['analysis_scope'] != 'community_total' or r['function_type'] != kind or r['function_namespace'] != namespace or '|' in r['function_id'] for r in rows):
                            raise ValueError('功能分析证据层级或命名空间不兼容')
                        unit_info = measurement(rows, kind)
                        if len({r['function_id'] for r in rows}) != len(rows) or unit_info['status'] == 'conflict':
                            raise ValueError('重复条目或混合单位')
                        special = {r['function_id']: r['value'] for r in rows if SPECIAL.match(r['function_id'])}
                        named = sorted((r for r in rows if not SPECIAL.match(r['function_id']) and r['value'] > 0), key=lambda r: r['value'], reverse=True)
                        if kind == 'pathway_coverage' and any(r['value'] > 1 for r in named):
                            raise ValueError('覆盖度超出 0–1 范围')
                        for row in named:
                            display_value(row['value'], unit_info)
                        top = [dict(id=r['function_id'], name=r['function_name'], value=r['value']) for r in named[:3]]
                        text = f'{sample}：{label}结果中有 {len(named)} 个非零的已命名条目，已排除 UNMAPPED、UNGROUPED、UNINTEGRATED 等诊断类别。'
                        if top:
                            text += '数值居前的条目为 ' + '、'.join(f"{r['id']}（{r['name']}；{r['value']:.4g}）" for r in top) + '。'
                        else:
                            text += '目前没有可供生物学解释的非零已命名条目，不能据此宣称通路缺失。'
                        text += unit_info['note']
                        if special:
                            text += '诊断类别另列：' + '、'.join(f'{key}={value:.4g}' for key, value in special.items()) + '；这些数值不是已命名功能，也不是可直接相加解释的未注释 reads 比例。'
                        add('functional', sample, text, dict(named_nonzero_count=len(named), top_features=top, special_entries=special, unit=unit_info['source_unit'], measurement=unit_info), [source])
                        sections[-1]['function_type'] = kind
                    covered[stage].add((sample, path.name))
                except (OSError, ValueError, KeyError, TypeError):
                    warnings.append(f'{path.relative_to(root)}：数据格式、单位或数值校验未通过，未据此生成解读。')
        for stage in sorted(succeeded & covered.keys()):
            for sample in sorted(samples):
                expected = ({f'{sample}.fastp.json'} if stage == 'fastp' else
                            {'species_abundance.tsv'} if stage == 'taxonomy' else set(FUNCTIONS))
                missing = sorted(name for name in expected if (sample, name) not in covered[stage])
                if missing:
                    warnings.append(f'{sample}：{stage} 阶段未取得可完整校验的 {"、".join(missing)}；可能缺失、格式不兼容或超过读取上限，不据此推断零值或生物学缺失。')
        limitations = [
            '这是基于已完成阶段的描述性初步解读，供研究者复核，不是完整论文结论或临床诊断。',
            '宏基因组 DNA 的功能注释反映群落功能潜力，不代表基因表达或代谢活性；不能将总体功能直接归属于某一物种或 MAG。',
            '未执行组间差异检验、多重检验校正或混杂因素调整，因此不报告显著富集、因果关系或疾病关联。',
            '数据库覆盖范围、测序深度及参考序列偏倚会影响检出和注释；未检出不能简单等同于不存在。',
        ]
        if len(samples) <= 1:
            limitations.append('当前不足两个样本，不作样本间距离排序或组间比较。')
        display_sections = enrich_display(_display_sections(sections), sections)
        text = '\n\n'.join(['结果初步解读（规则计算，版本 1.3）', f"任务状态：{task['status']}；样本数：{len(samples)}。"] + [s['text'] for s in display_sections] + ([] if sections else ['未获得可用于解读的已完成阶段数据。']) + ['注意事项：' + '；'.join(warnings + limitations)])
        return dict(task_id=task_id, generated_at=utc_now(), version='1.3', method='deterministic_templates', partial=task['status'] != 'completed', sample_count=len(samples), sections=sections, display_sections=display_sections, sources=sources, warnings=warnings, limitations=limitations, text=text)


def create_interpretation_router(service):
    router = APIRouter()

    def generate(task_id):
        try:
            return service.generate(task_id)
        except KeyError as exc:
            raise HTTPException(404, 'task not found') from exc

    @router.get('/api/tasks/{task_id}/interpretation')
    def interpretation(task_id: str):
        return generate(task_id)

    @router.get('/api/tasks/{task_id}/interpretation/prompt')
    def prompt(task_id: str):
        return build_prompt(generate(task_id))

    @router.post('/api/tasks/{task_id}/interpretation/refine')
    def refine(task_id: str):
        return service.ai.refine(generate(task_id))

    @router.get('/api/tasks/{task_id}/interpretation/download')
    def download(task_id: str):
        result = generate(task_id)
        evidence = '\n'.join(f"{s['id']} | {s['path']} | SHA-256: {s['sha256']}" for s in result['sources'])
        return Response(result['text'] + '\n\n数据来源：\n' + evidence, media_type='text/plain; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="preliminary-analysis.txt"'})

    return router
