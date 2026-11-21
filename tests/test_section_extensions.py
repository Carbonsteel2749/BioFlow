import importlib

import pytest

from bioflow_skills import run_skill
from article_writing import section_library
from article_writing.contracts import SectionId
from article_writing.skills.library_sections import write_library_section

ext = importlib.import_module('bioflow_skills.skills.writing.section_extensions')


class Model:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def generate(self, prompt, system=None):
        self.calls += 1
        assert 'untrusted data' in system
        return self.text


@pytest.mark.parametrize('name', list(ext.CONFIG))
def test_missing_evidence_does_not_invent_or_call_model(name):
    cfg = ext.CONFIG[name]
    model = Model('There were 999 records.')
    draft = write_library_section(section_library.get(cfg[0]), SectionId(cfg[0]),
                                  ledger={'facts': []}, model=model)
    assert draft.metadata['library']['status'] == 'written'
    assert 'AUTHOR_INPUT_NEEDED' in draft.markdown
    assert 'SKILL_PENDING' not in draft.markdown
    assert model.calls == 0


@pytest.mark.parametrize('name', list(ext.CONFIG))
def test_sections_become_ready_through_real_registry(name):
    from bioflow_skills.registry import get_registry
    rows = section_library.catalogue([s.name for s in get_registry().list_specs()])['sections']
    assert next(r for r in rows if r['id'] == ext.CONFIG[name][0])['status'] == 'ready'


def test_database_record_count_uses_own_evidence_not_cohort_size():
    result = run_skill('database-content-writer', {'section': 'database_content', 'ledger': {'facts': [
        {'id': 'COHORT.sample_count', 'text': 'The study included 42 samples.'},
        {'id': 'DATABASE.CONTENT.records', 'text': 'The database contains 120 curated records.'}]},
        'model': Model('The database contains 120 curated records.')})
    assert result.succeeded
    assert '120' in result.data['markdown']
    assert '42' not in result.data['markdown']
    assert result.data['fact_ids'] == ['DATABASE.CONTENT.records']


@pytest.mark.parametrize('name,fid,text', [
    ('results-discussion-writer','RESULT.observation','Diversity was measured in the cohort.'),
    ('implementation-writer','WORKFLOW.steps','The workflow filters reads before classification.'),
    ('benchmarking-writer','BENCH.runtime','The benchmark took 12 seconds.'),
    ('application-examples-writer','EXAMPLE.observation','The documented example processed a stool sample.'),
    ('availability-requirements-writer','LICENSE.code','The project uses the MIT license.'),
    ('database-construction-writer','CURATION.rules','Duplicate records were removed by accession.'),
    ('database-content-writer','DATABASE.CONTENT.records','The database contains 120 curated records.'),
    ('database-usage-writer','API.query','The documented interface supports identifier queries.'),
])
def test_all_eight_dispatch_with_evidence(name,fid,text):
    result=run_skill(name,{'section':ext.CONFIG[name][0],
                          'ledger':{'facts':[{'id':fid,'text':text}]},'model':Model(text)})
    assert result.succeeded
    assert result.data['markdown']==text
    assert result.data['evidence_state']=='checked_draft'


def test_unsupported_measurement_falls_back_to_source_facts():
    model = Model('The benchmark achieved 999 milliseconds [@Invented].')
    result = run_skill('benchmarking-writer', {'section': 'benchmarking', 'ledger': {'facts': [
        {'id': 'BENCH.runtime', 'text': 'The benchmark took 12 seconds.'}]}, 'model': model})
    assert result.succeeded
    assert '999' not in result.data['markdown']
    assert 'Invented' not in result.data['markdown']
    assert '12 seconds' in result.data['markdown']
    assert model.calls == 2


def test_ordinary_qc_is_not_presented_as_benchmark_or_application():
    ledger = {'facts': [{'id': 'QC.raw_reads', 'text': 'There were 120 reads.'}]}
    for name in ['benchmarking-writer', 'application-examples-writer']:
        result = run_skill(name, {'ledger': ledger})
        assert result.succeeded
        assert result.data['evidence_state'] == 'missing'
        assert '120' not in result.data['markdown']


def test_author_sections_and_other_gaps_remain_unchanged():
    from bioflow_skills.registry import get_registry
    rows = {r['id']: r for r in section_library.catalogue(
        [s.name for s in get_registry().list_specs()])['sections']}
    assert rows['ethics_statement']['status'] == 'author_input'
    assert rows['comparison_existing']['status'] == 'ready'
