import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'article_writing'))
sys.path.insert(0, str(ROOT / 'tests'))

from bioflow_skills import run_skill  # noqa: E402
from bioflow_skills.skills.writing.review_revise import review, review_and_revise, revise, storms_check  # noqa: E402
from test_writing_core_skills import package  # noqa: E402

TV = ('Raw reads ranged from 1,000 to 3,000 per sample. Filtering was highly effective and the data are of '
      'excellent quality.\n\n![PCA.](figures/fig_a.png)\n\n**Figure 1.** PCA.\n\n'
      'Species composition differed between samples (Figure 1) as described by earlier work [@Zhong2026].')


def status(report, item):
    return next(i['status'] for i in report['items'] if i['id'] == item)


def test_storms_distinguishes_reported_author_input_and_missing():
    sections = {
        'methods': '[AUTHOR_INPUT_NEEDED: study design; DNA extraction; ethics approval and informed consent.]\n\n'
                   'Reads were sequenced as paired-end 2 × 150 bp reads and filtered with fastp v1.3.6. Host reads '
                   'were removed against GRCh38 with Bowtie2 v2.5.5. Kraken2 v2.17.1 and HUMAnN v3.9 were used. '
                   'No statistical comparison between samples or groups was performed.',
        'data_records': 'Reads are available under project accession PRJEB1234 from 9 human stool samples.',
        'code_availability': '[AUTHOR_INPUT_NEEDED: public repository URL, version or commit.]',
    }
    report = storms_check(sections)
    assert status(report, 'qc') == 'reported' and status(report, 'host') == 'reported'
    assert status(report, 'raw_data') == 'reported' and status(report, 'statistics') == 'reported'
    assert status(report, 'participants') == 'reported'
    assert status(report, 'extraction') == 'author_input' and status(report, 'ethics') == 'author_input'
    assert status(report, 'code') == 'author_input'
    assert status(report, 'contamination') == 'missing'
    assert '| DNA extraction method | 待作者补充 (author_input) | methods |' in report['markdown']
    # a placeholder never counts as reported
    assert status(storms_check({'m': '[AUTHOR_INPUT_NEEDED: DNA extraction kit]'}), 'extraction') == 'author_input'


def test_storms_ignores_sentences_that_say_information_is_absent():
    # Regression from session 55de832d726d: back matter listing what is missing was counted as reported.
    sections = {'back_matter': 'Limitations include the absence of information regarding DNA extraction, ethics '
                               'approval and consent. Library preparation details are not included in the package.',
                'code_availability': 'All run parameters were recorded in the provenance file parameters.json '
                                     'released with the results.'}
    report = storms_check(sections)
    for item in ('extraction', 'ethics', 'library'):
        assert status(report, item) == 'author_input', item
    assert status(report, 'processed_data') == 'missing'


def test_unsupported_claims_with_ledger_numbers_are_overruled():
    # Regression from session 55de832d726d: 0.0012% is in the ledger, the reviewer said it was not reported.
    ledger = {'facts': [{'id': 'QC.host_removed_pct.max', 'label': 'highest host share', 'text': '0.0012% (s2)',
                         'value': 0.000012}]}
    tv = 'Host read pairs were removed. The highest percentage of read pairs removed as host was 0.0012% (s2).'
    issues = [{'section': 'technical_validation', 'quote': 'The highest percentage of read pairs removed as host was 0.0012% (s2).',
               'problem': 'The percentage is not reported in the text.', 'severity': 'major', 'kind': 'unsupported'}]
    data = review_and_revise({'technical_validation': tv}, {}, {'technical_validation'},
                             Both(issues, 'should not be used'), ledger)
    assert data['issues'][0]['action'].startswith('rejected') and data['sections']['technical_validation'] == tv
    # Regression from session 1b323fe25516: an interpretation is revised even when its numbers are in the ledger.
    tv2 = tv + ' This suggests very low host contamination in every sample of the study cohort.'
    issues2 = [{'section': 'technical_validation', 'quote': 'The highest percentage of read pairs removed as host was 0.0012% (s2).',
                'problem': 'Not supported.', 'severity': 'minor', 'kind': 'unsupported'}]
    issues2[0]['quote'] = 'This suggests very low host contamination in every sample of the study cohort.'
    data2 = review_and_revise({'technical_validation': tv2}, {}, {'technical_validation'},
                              Both(issues2, 'Host read pairs were removed. The highest percentage of read pairs '
                                            'removed as host was 0.0012% (s2), which is low.'), ledger)
    assert not data2['issues'][0]['action'].startswith('rejected')


def test_identical_rewrite_is_reported_as_unchanged():
    same = 'Filtering was highly effective and the data are of excellent quality'
    assert revise(TV, ISSUE, Reviser(same))['status'] == 'unchanged'


def test_revision_never_touches_the_other_sentences():
    # Regression from session dd4c399fae46: a whole-paragraph rewrite dropped the opening sentences.
    para = ('After filtering, at least 92.91% of bases reached Q30. Host read pairs were at most 0.0012%. '
            'This shows the samples contained very little human DNA. It does not measure how well host removal worked.')
    issue = {'section': 'discussion', 'quote': 'It does not measure how well host removal worked.',
             'problem': 'Unclear.', 'severity': 'minor', 'kind': 'unclear'}
    whole = Reviser('It does not measure the effectiveness of host removal, as other metrics would be needed.')
    out = revise(para, issue, whole)
    if out['status'] == 'revised':
        assert out['markdown'].startswith('After filtering, at least 92.91% of bases reached Q30. Host read pairs')
    long = Reviser('It does not measure how well host removal worked, because ' + 'other metrics would be needed ' * 6 + '.')
    assert revise(para, issue, long)['status'] == 'kept_original'


def test_redundant_sentence_may_be_deleted_only_safely():
    para = 'The data are descriptive. The study is descriptive only. These data cover nine samples.'
    issue = {'section': 'abstract', 'quote': 'The study is descriptive only.', 'problem': 'Repeats.',
             'severity': 'minor', 'kind': 'redundant'}
    # the next sentence starts with a pronoun, so deleting would leave it dangling
    assert revise(para, issue, Reviser('DELETE'))['status'] == 'kept_original'
    ok = revise('The data are descriptive. The study is descriptive only. Nine samples were analysed.', issue,
                Reviser('DELETE'))
    assert ok['status'] == 'revised' and ok['markdown'] == 'The data are descriptive. Nine samples were analysed.'
    unclear = dict(issue, kind='unclear')
    assert revise(para, unclear, Reviser('DELETE'))['status'] == 'kept_original'


class Reviewer:
    def __init__(self, issues):
        self.issues = issues

    def generate_json(self, prompt, system, schema):
        assert '![PCA' not in prompt  # figure lines are not sent to the reviewer
        return {'issues': self.issues}


def test_review_keeps_only_quotes_found_in_the_named_section():
    issues = [
        {'section': 'technical_validation', 'quote': 'Filtering was highly effective and the data are of excellent quality.',
         'problem': 'Overstated.', 'severity': 'major', 'kind': 'overclaim'},
        {'section': 'technical_validation', 'quote': 'This sentence does not exist in the text at all.',
         'problem': 'x', 'severity': 'minor', 'kind': 'unclear'},
        {'section': 'methods', 'quote': 'Filtering was highly effective and the data are of excellent quality.',
         'problem': 'wrong section', 'severity': 'minor', 'kind': 'unclear'},
    ]
    result = review({'technical_validation': TV, 'methods': 'Methods text here.'}, {}, Reviewer(issues))
    assert [i['problem'] for i in result['issues']] == ['Overstated.']
    assert len(result['discarded']) == 2


class Reviser:
    def __init__(self, reply):
        self.reply = reply

    def generate(self, prompt, system=None):
        return self.reply


ISSUE = {'section': 'technical_validation', 'quote': 'Filtering was highly effective and the data are of excellent quality.',
         'problem': 'Overstated.', 'severity': 'major', 'kind': 'overclaim'}


def test_revise_replaces_only_the_paragraph_and_keeps_figures():
    out = revise(TV, ISSUE, Reviser('Most reads passed filtering.'))
    assert out['status'] == 'revised'
    assert out['markdown'].startswith('Raw reads ranged from 1,000 to 3,000 per sample. Most reads passed filtering.\n\n')
    assert '![PCA.](figures/fig_a.png)' in out['markdown'] and out['markdown'].endswith('earlier work [@Zhong2026].')


def test_revise_rejects_new_numbers_citations_and_lost_figures():
    added_number = 'About 97.5% of reads passed filtering.'
    assert revise(TV, ISSUE, Reviser(added_number))['status'] == 'kept_original'
    issue3 = dict(ISSUE, quote='Species composition differed between samples (Figure 1) as described by earlier work [@Zhong2026].')
    lost_figure = 'Species composition differed between samples as described by earlier work [@Zhong2026].'
    out = revise(TV, issue3, Reviser(lost_figure))
    assert out['status'] == 'kept_original' and 'rewrite dropped a figure reference' in out['reasons']
    new_cite = 'Species composition differed between samples (Figure 1) [@Zhong2026; @Other2020].'
    assert revise(TV, issue3, Reviser(new_cite))['status'] == 'kept_original'
    overclaim = 'Read depth differed significantly between samples.'
    assert revise(TV, ISSUE, Reviser(overclaim))['status'] == 'kept_original'


class Both(Reviewer):
    def __init__(self, issues, reply):
        super().__init__(issues)
        self.reply = reply

    def generate(self, prompt, system=None):
        return self.reply


def test_review_and_revise_routes_by_kind_and_section():
    issues = [dict(ISSUE), {'section': 'methods', 'quote': 'Reads were filtered with fastp at default settings.',
                            'problem': 'Give parameters.', 'severity': 'minor', 'kind': 'unclear'},
              dict(ISSUE, kind='missing_information', problem='Add sequencing platform.')]
    sections = {'technical_validation': TV, 'methods': 'Reads were filtered with fastp at default settings.'}
    good = 'Most reads passed filtering.'
    data = run_skill('targeted-reviser', {'sections': sections, 'revisable': ['technical_validation'],
                                          'model': Both(issues, good)}).data
    actions = [i['action'] for i in data['issues']]
    assert actions == ['revised', 'author (section not model-written)', 'author']
    assert data['sections']['methods'] == sections['methods']
    assert 'Action: revised' in data['markdown'] and 'Revised sentence: Most reads passed filtering.' in data['markdown']


def test_number_citations_for_export():
    from article_writing.layout.citations import number_citations
    md = ('# Introduction\n\nKnown A [@B2020]. Known B [@A2019; @B2020].\n\n中文 [@A2019]\n\n# Back Matter\n\n'
          'Data are available.\n\nReferences\n\n[@A2019]: A. Paper A. 2019.\n\n[@B2020]: B. Paper B. 2020.\n\n'
          '[@C2021]: C. Never cited. 2021.\n')
    out = number_citations(md)
    assert 'Known A [1]. Known B [2, 1].' in out and '中文 [2]' in out
    assert out.rstrip().endswith('# References\n\n1. B. Paper B. 2020.\n2. A. Paper A. 2019.')
    assert '@' not in out and 'Never cited' not in out
    assert number_citations('No citations here.\n') == 'No citations here.\n'
    assert '# 参考文献\n\n1. B. Paper B. 2020.' in number_citations(md, heading='参考文献')


class PipelineModel:
    enabled = True

    def generate(self, prompt, system=None):
        if 'Reviewer comment' in prompt:
            return 'The samples were described using the facts listed.'
        return 'The samples were described with the listed facts.'

    def generate_json(self, prompt, system, schema):
        if 'issues' in schema.get('properties', {}):
            return {'issues': [{'section': 'technical_validation',
                                'quote': 'The samples were described with the listed facts.',
                                'problem': 'Vague.', 'severity': 'minor', 'kind': 'unclear'}]}
        return {'sentences': []}


def test_pipeline_runs_quality_gates_before_translation(tmp_path):
    from article_writing import section_library
    from article_writing.orchestrator.pipeline import WritingPipeline
    pipe = WritingPipeline(fixtures_dir=ROOT / 'article_writing/fixtures', adapter_mode='live',
                           analysis_run_dir=str(package(tmp_path)), literature_enabled=False, react_enabled=False,
                           brief_text='Stool metagenomes from ENA project PRJEB1234', llm=PipelineModel(),
                           llm_language='en', section_order=section_library.resolve_sections('data_descriptor'))
    state = pipe.run(run_id='q')
    tv = state.drafts['technical_validation']
    assert 'The samples were described using the facts listed.' in tv.markdown
    assert tv.metadata['review'][0]['action'] == 'revised'
    assert pipe.quality_report['review']['revised'] == 1
    assert pipe.quality_report['storms']['reported'] > 0
    assert 'peer_review.md' in pipe.skill_outputs and 'storms_checklist.md' in pipe.skill_outputs
