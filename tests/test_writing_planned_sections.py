import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'article_writing'))
sys.path.insert(0, str(ROOT / 'tests'))

from bioflow_skills import run_skill  # noqa: E402
from bioflow_skills.skills.writing.claim_verifier import verify  # noqa: E402
from bioflow_skills.skills.writing.fact_ledger import build_ledger  # noqa: E402
from bioflow_skills.skills.writing.paragraph_writer import build_prompt  # noqa: E402
from bioflow_skills.skills.writing.section_planner import build_plan  # noqa: E402
from test_writing_core_skills import package  # noqa: E402


def ledger_for(tmp_path):
    return build_ledger(package(tmp_path))


def test_results_plan_has_small_fact_sets_and_code_derived_readings(tmp_path):
    plan = build_plan(ledger_for(tmp_path), 'results')
    keys = [p['key'] for p in plan]
    assert keys[:3] == ['depth', 'base_quality', 'host']
    assert all(len(p['fact_ids']) <= 7 for p in plan)
    host = next(p for p in plan if p['key'] == 'host')
    # Fixture host share is 2.2%: above the 1% threshold, so no "little human DNA" reading.
    assert any('Host read pairs reached' in note for note in host['notes'])
    assert not any('very little human DNA' in note for note in host['notes'])
    assert any('effective' in rule for rule in host['forbid'])
    quality = next(p for p in plan if p['key'] == 'base_quality')
    assert any('high base-call quality' in note for note in quality['notes'])


def test_low_host_share_reads_as_little_human_dna_not_effectiveness(tmp_path):
    ledger = ledger_for(tmp_path)
    for fact in ledger['facts']:
        if fact['id'] == 'QC.host_removed_pct.max':
            fact['value'], fact['text'] = 0.000012, '0.0012% (s2)'
    host = next(p for p in build_plan(ledger, 'results') if p['key'] == 'host')
    note = next(n for n in host['notes'] if 'human DNA' in n)
    assert 'at most 0.0012%' in note and 'does not measure how well host removal worked' in note


def test_discussion_plan_states_limitations_from_missing_inputs(tmp_path):
    plan = build_plan(ledger_for(tmp_path), 'discussion')
    limits = next(p for p in plan if p['key'] == 'limitations')
    assert any('DNA extraction' in note for note in limits['notes'])
    assert any('No group comparison' in note or 'statistical' in note for note in limits['notes'])


def test_planner_skill_is_registered(tmp_path):
    result = run_skill('section-planner', {'ledger': ledger_for(tmp_path), 'section': 'abstract',
                                           'brief': {'title': 'T', 'research_question': 'Describe gut samples'}})
    assert result.succeeded and 'Describe gut samples' in result.data['paragraphs'][0]['purpose']


def test_verifier_flags_unsourced_numbers_overclaims_and_ids(tmp_path):
    paragraph = build_plan(ledger_for(tmp_path), 'results')[0]
    good = 'Raw reads ranged from 1,000 (s1) to 3,000 (s2) across 3 samples.'
    assert verify(good, paragraph)['ok']
    bad = ('Raw reads ranged from 1,000 to 4,500. Depth varied significantly between samples. '
           'The total was large [QC.raw_reads.total].')
    result = verify(bad, paragraph)
    assert not result['ok'] and result['bad_sentences'] == [0, 1, 2]
    assert any('4,500' in issue for issue in result['issues'])
    assert any('significantly' in issue for issue in result['issues'])


def _composition_paragraph():
    return {'key': 'composition', 'purpose': 'Describe species.', 'max_words': 140,
            'context': 'Shotgun profiling of nine gut samples from ENA project PRJEB33098',
            'facts': ['species ranked 1 by mean fraction: Faecalibacterium prausnitzii (mean 7.70% of reads; '
                      'above 0.1% in 9 of 9 samples)',
                      'species ranked 5 by mean fraction: Bacteroides uniformis (mean 3.78% of reads)',
                      'species that were most abundant in each sample: Faecalibacterium prausnitzii in 3 samples; '
                      'Escherichia coli in 2 samples; Akkermansia muciniphila in 1 sample'],
            'notes': [],
            'checks': {'dominance': {'Faecalibacterium prausnitzii': 3, 'Escherichia coli': 2,
                                     'Akkermansia muciniphila': 1},
                       'species': ['Akkermansia muciniphila', 'Bacteroides uniformis', 'Escherichia coli',
                                   'Faecalibacterium prausnitzii']}}


def test_dominance_claims_are_checked_against_computed_counts():
    # Regression from session d28ff811f179: B. uniformis was never the most abundant species.
    paragraph = _composition_paragraph()
    wrong = ('F. prausnitzii was the most abundant species in three samples, '
             'and B. uniformis was the most abundant in one sample.')
    result = verify(wrong, paragraph)
    assert not result['ok'] and any('B. uniformis' in i for i in result['issues'])
    miscount = 'Escherichia coli was the most abundant species in four samples.'
    assert any('the facts say 2' in i for i in verify(miscount, paragraph)['issues'])
    right = ('F. prausnitzii was the most abundant species in three samples and A. muciniphila in one sample, '
             'while F. prausnitzii was present above 0.1% in all nine samples.')
    assert verify(right, paragraph)['ok']


def test_prevalence_claims_are_checked_per_clause():
    # Regression from session f5867540bfde: E. coli was above 0.1% in 6 of 9 samples, not all.
    paragraph = _composition_paragraph()
    paragraph['checks']['prevalence'] = {'Faecalibacterium prausnitzii': 9, 'Escherichia coli': 6,
                                         'Bacteroides uniformis': 7}
    paragraph['checks']['sample_count'] = 9
    wrong = ('The communities shared several species, with Faecalibacterium prausnitzii and Escherichia coli '
             'each detected above 0.1% of reads in all samples.')
    issues = verify(wrong, paragraph)['issues']
    assert any('escherichia coli' in i.lower() and '6 of 9' in i for i in issues)
    assert not any('prausnitzii' in i for i in issues)
    # A species named in an earlier clause is not part of the "all samples" claim.
    right = ('Escherichia coli and Bacteroides uniformis ranked second and fifth by mean fraction, '
             'with F. prausnitzii present above 0.1% in all nine samples.')
    assert verify(right, paragraph)['ok']


import pytest  # noqa: E402


@pytest.mark.parametrize('sentence,tool', [
    # Regressions from sessions fd8f60694697 and f5867540bfde.
    ('Taxonomic profiling was conducted with kraken2 version 2.17.1 and bracken version 3.0.1, which estimated '
     'species abundances and calculated the Shannon index based on species fractions.', 'Bracken'),
    ('Functional profiling using humann version 3.9 showed a range of Shannon index values from 2.89 to 4.17.',
     'HUMAnN'),
    ('The Shannon index was calculated by HUMAnN.', 'HUMAnN'),
    ('Host reads were removed and Bowtie2 estimated the most abundant species.', 'Bowtie2'),
    # Regression from session a6e7a13b7faa: second object of the same verb.
    ('Taxonomic profiling was conducted with kraken2 version 2.17.1 and bracken version 3.0.1, which generated '
     'species abundances and the Shannon index.', 'Bracken'),
])
def test_wrong_tool_attribution_is_flagged(sentence, tool):
    result = verify(sentence, {'facts': [], 'notes': [], 'max_words': 200})
    assert not result['ok'] and any(f'to {tool}' in i for i in result['issues'])


@pytest.mark.parametrize('sentence', [
    'The Shannon index was computed from the Bracken species fractions.',
    'Kraken2 and Bracken estimated species abundances, and the Shannon index was computed per sample.',
    'Q30 base fractions were reported by fastp.',
    'Host reads were removed using bowtie2, and taxonomic profiling with Kraken2 estimated species abundances.',
    'Functional profiles were generated with HUMAnN using MetaCyc pathways.',
    'Reads were filtered with fastp, and Shannon indices were computed from species fractions.',
    # Regression from session 4ec0c5b371ec: "post-fastp" is a label, not the agent.
    'Sample ERR3450203 had 55,020,608 raw reads, which reduced to 52,096,180 post-fastp reads and 26,048,090 '
    'post-fastp read pairs, with 139 read pairs identified as host-removed.',
])
def test_correct_tool_attribution_passes(sentence):
    result = verify(sentence, {'facts': [], 'notes': [], 'max_words': 200})
    assert not any('attributes' in i for i in result['issues']), result['issues']


def test_dropping_a_sentence_takes_the_pronoun_sentence_after_it():
    from bioflow_skills.skills.writing.paragraph_loop import with_dependents
    text = 'Depth was 999,999 reads. It does not measure quality. Q30 was high.'
    assert with_dependents(text, {0}) == {0, 1}
    assert with_dependents(text, {2}) == {2}


def test_topic_title_never_cuts_an_accession():
    from article_writing.adapters.brief_from_analysis import brief_from_topic
    topic = ('Shotgun metagenomic profiling of nine human gut samples from ENA project PRJEB33098: read quality '
             'control, host read removal and functional profiling.')
    assert brief_from_topic(topic).title.endswith('PRJEB33098')
    long = 'Profiling of human gut samples with version 2.5 tools ' * 4
    title = brief_from_topic(long).title
    assert len(title) <= 120 and long.startswith(title) and not title.endswith(' ')
    assert '2.5' in title  # a decimal point is not a sentence end


def test_identifiers_must_match_evidence():
    # Regression from session d28ff811f179: PRJEB33098 was truncated to PRJEB33.
    paragraph = _composition_paragraph()
    assert verify('Samples came from ENA project PRJEB33098.', paragraph)['ok']
    result = verify('Samples came from ENA project PRJEB33.', paragraph)
    assert not result['ok'] and any('PRJEB33 ' in i or 'PRJEB33,' in i for i in result['issues'])


def test_plan_paragraphs_carry_checks_and_context(tmp_path):
    plan = build_plan(ledger_for(tmp_path), 'results', {'title': 'Project PRJEB1'})
    assert all(p['context'] == 'Project PRJEB1' and 'dominance' in p['checks'] for p in plan)


def test_model_judge_is_used_but_cannot_reject_whole_paragraph(tmp_path):
    paragraph = build_plan(ledger_for(tmp_path), 'results')[0]
    text = 'Raw reads ranged from 1,000 (s1) to 3,000 (s2). Read depth was adequate. Three samples were sequenced.'

    class Judge:
        def __init__(self, rows):
            self.rows = rows

        def generate_json(self, prompt, system, schema):
            assert '0. Raw reads ranged' in prompt
            return {'sentences': self.rows}

    one = verify(text, paragraph, Judge([{'index': 1, 'supported': False, 'problem': 'adequate is a judgement'}]))
    assert not one['ok'] and one['bad_sentences'] == [1]
    everything = verify(text, paragraph, Judge([{'index': i, 'supported': False, 'problem': 'x'} for i in range(3)]))
    assert everything['ok'] and 'unreliable' in everything['judge']


def test_writer_prompt_carries_only_paragraph_evidence_and_style(tmp_path):
    plan = build_plan(ledger_for(tmp_path), 'results')
    prompt = build_prompt(plan[2], section='results', title='T', style_rules='Use active voice.')
    assert 'Host read pairs reached' in prompt and 'Use active voice.' in prompt
    assert 'Q30' not in prompt  # base-quality facts belong to another paragraph
    revise = build_prompt(plan[0], section='results', title='T', draft='Old text.', issues=['Sentence 1 is wrong.'])
    assert 'Old text.' in revise and 'Sentence 1 is wrong.' in revise


class ScriptedModel:
    """First reply for each paragraph is wrong; the revision is right."""

    enabled = True

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, system=None):
        self.prompts.append(prompt)
        if 'Your previous version' in prompt:
            return 'The samples were sequenced and filtered as listed.'
        return 'Read depth reached 999,999 reads and varied significantly.'

    def generate_json(self, prompt, system, schema):
        return {'sentences': []}


def test_runner_revises_then_passes(tmp_path):
    from article_writing.contracts import PaperBrief, SectionId
    from article_writing.skills.section_runner import write_planned_section
    model = ScriptedModel()
    markdown, meta, warnings = write_planned_section(SectionId.results, ledger_for(tmp_path), model,
                                                     PaperBrief(title='T'), style_rules='Be concise.')
    assert '999,999' not in markdown and 'significantly' not in markdown
    assert all(p['revisions'] == 1 and p['final_ok'] for p in meta['paragraphs'])
    assert any('Be concise.' in p for p in model.prompts)
    assert not warnings


def test_runner_drops_sentences_that_never_pass(tmp_path):
    from article_writing.contracts import PaperBrief, SectionId
    from article_writing.skills.section_runner import write_planned_section

    class Stubborn(ScriptedModel):
        def generate(self, prompt, system=None):
            return 'The 3 samples were analysed. Depth reached 999,999 reads.'

    markdown, meta, _ = write_planned_section(SectionId.conclusion, ledger_for(tmp_path), Stubborn(),
                                              PaperBrief(title='T'))
    assert '999,999' not in markdown
    assert meta['paragraphs'][0]['dropped_sentences'] == [1]


def test_pipeline_writes_planned_sections_for_material_packages(tmp_path):
    from article_writing.orchestrator.pipeline import WritingPipeline
    root = package(tmp_path)

    class Model(ScriptedModel):
        def generate(self, prompt, system=None):
            self.prompts.append(prompt)
            return 'The samples were described with the listed facts.'

    pipe = WritingPipeline(fixtures_dir=ROOT / 'article_writing/fixtures', adapter_mode='live',
                           analysis_run_dir=str(root), literature_enabled=False, react_enabled=False,
                           brief_text='Describe metagenomic profiles', llm=Model(), llm_language='en')
    state = pipe.run(run_id='planned')
    for name in ('results', 'discussion', 'abstract', 'conclusion'):
        assert state.drafts[name].metadata['catalog_compose']['source'] == 'planned_paragraphs'
        assert state.drafts[name].metadata['planned']['paragraphs']
    assert state.drafts['methods'].metadata['catalog_compose']['source'] == 'methods-from-provenance'
