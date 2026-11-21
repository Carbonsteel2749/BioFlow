"""Remaining eight manuscript writers; derive only from supplied evidence."""
from collections import Counter
import re

from .section_extensions import CONFIG, BUILDERS, _register


ADDITIONAL = {
 'keywords-writer': ('keywords','关键词',r'^TOPIC[._]',
   'Select a small set of terms that actually occur in the supplied title or abstract.',
   '题目和摘要中的关键词', ['scientific-writing']),
 'highlights-writer': ('highlights','研究亮点',r'^(RESULT|DIV|TAX|FUNC|QC|STAT)[._]',
   'Summarize supported results as concise highlights without inventing novelty.',
   '已核实研究结果', ['scientific-writing']),
 'resource-comparison-writer': ('comparison_existing','与现有资源比较',r'^(DATABASE|DB|BENCH|EVAL|RESOURCE)[._]',
   'Compare only documented resources and cited claims on explicitly shared criteria. Do not imply superior coverage, novelty or performance without comparable measurements.',
   '本库信息、比较对象、同口径比较维度和可引用文献', ['scientific-critical-thinking','database-lookup']),
 'review-topics-writer': ('review_topics','综述主题',r'^(REVIEW|TOPIC)[._]',
   'Synthesize the supplied claim-bearing reference cards by theme, noting conflicting findings and evidence limits. Every literature claim needs its supplied citation; do not claim a systematic search unless documented.',
   '已核实文献卡片及主题范围', ['literature-review','scientific-writing']),
 'limitations-writer': ('limitations','局限性',r'^(LIMIT|SCOPE|MISSING)[._]',
   'Describe explicit limitations or documented evidence-package gaps. Distinguish not recorded in this package from not done in the study. Never infer an absent control, small sample or bias from silence.',
   '研究局限、分析边界或材料缺项记录', ['scientific-critical-thinking']),
 'outlook-writer': ('outlook','展望与未来方向',r'^(OUTLOOK|FUTURE|LIMIT|SCOPE)[._]',
   'Describe evidence-linked next questions as proposals, not completed results or promises. State which observed limitation or cited open question motivates each proposed step.',
   '已记录局限、开放问题与有依据的后续方向', ['scientific-writing']),
 'abbreviations-writer': ('abbreviations','缩写表',r'^ABBREVIATION[._]',
   'Extract only explicit long-form (ABBR) pairs from supplied manuscript text.',
   '稿件中已定义的全称与缩写', ['scientific-writing']),
 'supplementary-writer': ('supplementary_information','补充材料说明',r'^SUPPLEMENT[._]',
   'List only files explicitly designated as supplementary materials, with supplied labels and descriptions.',
   '已明确指定的补充文件清单及说明', ['scientific-writing']),
}


def _source(payload):
    brief=payload.get('brief') or {}
    return '\n'.join([str(brief.get('title') or ''), str(brief.get('research_question') or ''),
                      *[str(v) for v in (payload.get('written') or {}).values()]])


def _result(markdown, missing='', facts=None):
    return {'markdown':markdown,'warnings':[missing] if missing else [],'citations':[],
            'evidence_state':'missing' if missing and markdown.startswith('[AUTHOR') else 'extracted',
            'fact_ids':list(facts or [])}


def keywords(payload):
    brief=payload.get('brief') or {}
    text='\n'.join([str(brief.get('title') or ''), str(brief.get('research_question') or ''),
                     str((payload.get('written') or {}).get('abstract') or '')])
    stop=set('the and from with this that these those study analysis describe using used were was for are has have into only results methods data samples their our its through between based research'.split())
    terms=[w for w in re.findall(r'\b[A-Za-z][A-Za-z-]{3,}\b',text.lower()) if w not in stop]
    if not terms:
        return _result('[AUTHOR_INPUT_NEEDED: 请提供题目、摘要或作者选定的关键词。]','keywords: 缺少可提取术语的题目或摘要。')
    chosen=[w for w,_ in Counter(terms).most_common(6)]
    return _result('; '.join(chosen), 'keywords: 请作者确认提取的术语适合作为投稿关键词。' if len(chosen)<3 else '')


def highlights(payload):
    facts=[f for f in (payload.get('ledger') or {}).get('facts',[]) if isinstance(f,dict)
           and re.match(r'^(RESULT|DIV|TAX|FUNC|QC|STAT)[._]',str(f.get('id',''))) and f.get('text')]
    # Prefer summaries, then first occurrence; never manufacture a third finding.
    facts.sort(key=lambda f:('.range' not in str(f['id']) and 'summary' not in str(f['id'])))
    seen=set();selected=[]
    for f in facts:
        text=str(f['text'])
        if text in seen:continue
        seen.add(text);selected.append(f)
        if len(selected)==5:break
    if not selected:
        return _result('[AUTHOR_INPUT_NEEDED: 请提供已核实的研究结果以撰写研究亮点。]','highlights: 缺少结果事实。')
    return _result('\n'.join('- '+str(f.get('label') or 'Observed finding')+': '+str(f['text']) for f in selected),
                   'highlights: 目前不足三项独立结果，请作者确认。' if len(selected)<3 else '',
                   [f['id'] for f in selected])


def abbreviations(payload):
    text=_source(payload)
    pairs={}
    for match in re.finditer(r'([A-Za-z][A-Za-z -]{3,100})\s*\(([A-Z][A-Z0-9-]{1,11})\)',text):
        long,short=match.groups()
        # Keep the final words only when their initials actually establish the pair.
        words=long.strip().split()
        for n in range(1,min(len(words),12)+1):
            candidate=words[-n:]
            initials=''.join(w[0] for w in candidate if w.lower() not in {'of','and','the','for','in','a'}).upper()
            if initials==short.replace('-',''):
                pairs[short]=' '.join(candidate);break
    if not pairs:
        return _result('[AUTHOR_INPUT_NEEDED: 当前稿件未检测到明确的“全称（缩写）”定义，请作者补充或取消本节。]',
                       'abbreviations: 未从稿件中检测到可核实的缩写全称。')
    return _result('\n'.join(f'- {s}: {v}' for s,v in sorted(pairs.items())))


def supplementary(payload):
    materials=payload.get('materials') or {}
    records=materials.get('supplementary_files') or []
    rows=[]
    for item in records:
        if isinstance(item,dict) and item.get('file'):
            # A basename is enough; never expose local/private absolute paths.
            name=str(item['file']).replace('\\','/').rsplit('/',1)[-1]
            label=str(item.get('label') or name)
            description=str(item.get('description') or '[AUTHOR_INPUT_NEEDED: 文件内容说明]')
            rows.append('- '+label+': '+name+' — '+description)
    for f in (payload.get('ledger') or {}).get('facts',[]):
        if isinstance(f,dict) and str(f.get('id','')).startswith('SUPPLEMENT.') and f.get('text'):
            rows.append('- '+str(f['text']))
    if not rows:
        return _result('[AUTHOR_INPUT_NEEDED: 请指定补充图、补充表和补充文件；不会自动把分析输出列为补充材料。]',
                       'supplementary_information: 尚未指定补充文件。')
    return _result('\n'.join(rows))


CONFIG.update(ADDITIONAL)
BUILDERS.update({'keywords-writer':keywords,'highlights-writer':highlights,
                 'abbreviations-writer':abbreviations,'supplementary-writer':supplementary})
for _name,_config in ADDITIONAL.items():
    _register(_name,_config)
