"""Results: claim headings, one QC paragraph, no filler. Discussion: principal finding first,
only relevant literature, numbered limitations."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'article_writing'))
sys.path.insert(0, str(ROOT / 'tests'))

from bioflow_skills.skills.writing.claim_verifier import deterministic_issues, strip_filler  # noqa: E402
from bioflow_skills.skills.writing.fact_ledger import build_ledger  # noqa: E402
from bioflow_skills.skills.writing.paragraph_loop import write_plan  # noqa: E402
from bioflow_skills.skills.writing.section_planner import NO_COMPARABLE, _checks, _Ledger, build_plan  # noqa: E402
from test_writing_core_skills import package  # noqa: E402


def ledger_for(tmp_path):
    return build_ledger(package(tmp_path))


def test_results_paragraphs_carry_claim_headings(tmp_path):
    plan = build_plan(ledger_for(tmp_path), 'results')
    assert [p['key'] for p in plan][:2] == ['data_quality', 'composition']
    assert all(p.get('heading') for p in plan)
    assert len({p['heading'] for p in plan}) == len(plan)


def test_technical_validation_has_no_result_headings(tmp_path):
    assert not any(p.get('heading') for p in build_plan(ledger_for(tmp_path), 'technical_validation'))


def test_filler_clauses_are_cut_and_empty_sentences_dropped():
    text = ('Shannon indices ranged from 2.89 (s1) to 4.17 (s2), indicating variability in within-sample diversity. '
            'The number of reads retained varied across samples. '
            'Between 318 and 404 pathways were detected. '
            'These findings suggest variability in the extent of pathway coverage across the samples. '
            'These data offer insights into the microbial composition of individual samples.')
    cleaned, removed = strip_filler(text)
    assert cleaned == 'Shannon indices ranged from 2.89 (s1) to 4.17 (s2). Between 318 and 404 pathways were detected.'
    assert removed == 4


def test_specific_sentences_survive_filler_stripping():
    text = ('In s3, Escherichia coli accounted for 25.45% of reads, which may explain its lower Shannon index. '
            'Shotgun sequencing identified more taxa than 16S sequencing [@Durazzi2021].')
    assert strip_filler(text) == (text, 0)


def test_verifier_asks_the_writer_to_remove_filler(tmp_path):
    paragraph = build_plan(ledger_for(tmp_path), 'results')[0]
    issues, bad = deterministic_issues('The number of reads retained varied across samples.', paragraph)
    assert any('says nothing specific' in i for i in issues)
    assert not bad  # repaired in code, not by dropping


class Model:
    def generate(self, prompt, system=None):
        return 'The samples were described with the listed facts, indicating variability between samples.'

    def generate_json(self, prompt, system, schema):
        return {'sentences': []}


def test_write_plan_emits_headings_and_strips_filler(tmp_path):
    plan = build_plan(ledger_for(tmp_path), 'results')
    result = write_plan(plan, Model(), section='results', judge=False)
    md = result['markdown']
    assert md.startswith('### ' + plan[0]['heading'] + '\n\n')
    assert md.count('### ') == len(plan)
    assert 'indicating variability' not in md
    assert all(p['filler_removed'] for p in result['paragraphs'])


def test_discussion_opens_with_principal_finding_and_numbers_limitations(tmp_path):
    plan = build_plan(ledger_for(tmp_path), 'discussion')
    keys = [p['key'] for p in plan]
    assert keys[0] == 'principal' and keys[-1] == 'limitations'
    assert 'data_quality' not in keys and 'outlook' not in keys
    limits = plan[-1]
    assert 'First' in limits['purpose'] and any('group comparisons' in n for n in limits['notes'])


def _card(key, text, kind='finding'):
    return {'key': key, 'year': 2020, 'short_authors': key, 'title': key, 'abstract': '',
            'claims': [{'id': f'{key}.1', 'kind': kind, 'text': text, 'quote': text}]}


def test_discussion_cites_only_literature_related_to_what_was_observed(tmp_path):
    ledger = ledger_for(tmp_path)
    species = _checks(_Ledger(ledger))['species'][0]
    cards = [_card('Crc2019', 'Twenty-nine species were enriched in colorectal cancer metagenomes.'),
             _card('Shared2020', f'{species} was common in adult stool.'),
             _card('Method2021', 'Shotgun sequencing identified more taxa than 16S rRNA sequencing.')]
    plan = build_plan(ledger, 'discussion', cards=cards)
    lit = next(p for p in plan if p['key'] == 'literature')
    assert lit['citations'] == ['Method2021', 'Shared2020']
    assert any('also observed' in n for n in lit['notes'])
    assert any('align with' in rule for rule in lit['forbid'])


def test_unrelated_literature_is_left_out_and_said_so(tmp_path):
    cards = [_card('Crc2019', 'Twenty-nine species were enriched in colorectal cancer metagenomes.')]
    plan = build_plan(ledger_for(tmp_path), 'discussion', cards=cards)
    assert 'literature' not in [p['key'] for p in plan]
    assert NO_COMPARABLE in plan[-1]['notes']


def test_disease_study_using_shotgun_data_is_not_a_method_claim(tmp_path):
    cards = [_card('Wirbel2019', 'A meta-analysis of eight fecal shotgun metagenomic studies of colorectal cancer '
                                 'identified 29 enriched species.')]
    plan = build_plan(ledger_for(tmp_path), 'discussion', cards=cards)
    assert 'literature' not in [p['key'] for p in plan]


def test_literature_sentences_may_not_claim_agreement(tmp_path):
    cards = [_card('Method2021', 'Shotgun sequencing identified more taxa than 16S rRNA sequencing.')]
    lit = next(p for p in build_plan(ledger_for(tmp_path), 'discussion', cards=cards) if p['key'] == 'literature')
    bad = 'The present data align with the findings of [@Method2021].'
    issues, flagged = deterministic_issues(bad, lit)
    assert flagged == {0} and any('cannot agree with' in i for i in issues)
    good = 'Shotgun sequencing, as used here, identified more taxa than 16S rRNA sequencing [@Method2021].'
    assert not any('cannot agree with' in i for i in deterministic_issues(good, lit)[0])


def test_principal_paragraph_gets_few_numbers(tmp_path):
    principal = build_plan(ledger_for(tmp_path), 'discussion')[0]
    assert not any(i in principal['fact_ids'] for i in ('TAX.cohort_top.2', 'TAX.cohort_top.3'))


def test_translated_headings_stay_in_their_language():
    from article_writing.layout.bilingual import select_language
    md = ('### Diversity was lowest in one sample\n\nShannon ranged from 2.89 to 4.17.\n\n中文\n\n'
          '### 一个样本的多样性最低\n\n香农指数范围为2.89至4.17，这是中文段落。\n')
    en, zh = select_language(md, 'en'), select_language(md, 'zh')
    assert '### Diversity was lowest in one sample' in en and '一个样本' not in en
    assert '### 一个样本的多样性最低' in zh and 'Diversity was lowest' not in zh
    # untranslated subsection headings (Methods) are still shared by both languages
    shared = '### Participants\n\nAdults were recruited for the study.\n\n中文\n\n成年人被招募参加本研究，这是中文。\n'
    assert '### Participants' in select_language(shared, 'zh')


def test_latex_preview_turns_headings_into_subsections(tmp_path):
    from frontend.latex_preview import markdown_to_latex
    tex = markdown_to_latex('### The most abundant species differed between samples\n\nText 100%.', tmp_path)
    assert '\\subsection*{The most abundant species differed between samples}' in tex
    assert 'Text 100\\%.' in tex
