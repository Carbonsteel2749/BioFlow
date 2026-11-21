import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'article_writing'))
sys.path.insert(0, str(ROOT / 'tests'))

from article_writing import section_library  # noqa: E402
from article_writing.contracts import DEFAULT_SECTION_ORDER, SectionId  # noqa: E402
from test_writing_core_skills import package  # noqa: E402

LEGACY = ['abstract', 'introduction', 'methods', 'results', 'discussion', 'conclusion', 'back_matter']


def test_enum_keeps_legacy_sections_and_adds_library_sections():
    assert [s.value for s in SectionId][:7] == LEGACY
    assert [s.value for s in DEFAULT_SECTION_ORDER] == LEGACY
    assert SectionId.results == 'results' and SectionId('data_records').value == 'data_records'
    assert len(list(SectionId)) == len(section_library.sections())


def test_library_and_types_are_consistent():
    lib = section_library.sections()
    for spec in lib.values():
        assert all(dep in lib for dep in spec.depends_on), spec.id
        assert spec.mode != 'skill' or spec.skill, spec.id
    types = section_library.article_types()
    assert list(types['research_article'].sections) == LEGACY
    assert 'data_records' in types['data_descriptor'].sections


def test_writing_order_reproduces_legacy_order_and_puts_summaries_last():
    assert section_library.writing_order(LEGACY) == [
        'methods', 'results', 'discussion', 'introduction', 'abstract', 'conclusion', 'back_matter']
    order = section_library.writing_order(section_library.article_types()['data_descriptor'].sections)
    assert order.index('methods') < order.index('data_records') < order.index('background_summary') < order.index('abstract')


def test_resolve_sections():
    assert section_library.resolve_sections('data_descriptor')[1] == 'background_summary'
    assert section_library.resolve_sections('', ['results', 'abstract']) == ['results', 'abstract']
    with pytest.raises(ValueError, match='unknown sections'):
        section_library.resolve_sections('', ['nope'])
    with pytest.raises(ValueError, match='duplicate'):
        section_library.resolve_sections('', ['results', 'results'])
    with pytest.raises(ValueError, match='unknown article type'):
        section_library.resolve_sections('magazine')


def test_catalogue_marks_missing_skills():
    rows = {r['id']: r for r in section_library.catalogue(['data-records-writer'])['sections']}
    assert rows['results']['status'] == 'ready'
    assert rows['data_records']['status'] == 'ready'
    assert rows['technical_validation']['status'] == 'skill_missing'
    assert rows['funding']['status'] == 'author_input'


class Model:
    enabled = True

    def generate(self, prompt, system=None):
        return 'In this study, the samples were described with the listed facts.'

    def generate_json(self, prompt, system, schema):
        return {'sentences': []}


def run(tmp_path, order):
    from article_writing.orchestrator.pipeline import WritingPipeline
    pipe = WritingPipeline(fixtures_dir=ROOT / 'article_writing/fixtures', adapter_mode='live',
                           analysis_run_dir=str(package(tmp_path)), literature_enabled=False, react_enabled=False,
                           brief_text='Describe gut profiles', llm=Model(), llm_language='en', section_order=order)
    return pipe.run(run_id='lib')


def test_custom_order_with_placeholders_and_author_input(tmp_path):
    order = ['abstract', 'keywords', 'methods', 'data_records', 'limitations', 'funding']
    state = run(tmp_path, order)
    assert [s.value for s in state.section_order] == order
    assert set(state.drafts) == set(order)
    pending = state.drafts['keywords']
    assert 'SKILL_PENDING' not in pending.markdown
    assert pending.metadata['library']['status'] == 'written'
    assert pending.markdown.strip()
    assert state.drafts['funding'].markdown.startswith('[AUTHOR_INPUT_NEEDED: Funding')
    assert state.drafts['methods'].metadata['catalog_compose']['source'] == 'methods-from-provenance'
    assert state.drafts['abstract'].metadata['catalog_compose']['source'] == 'planned_paragraphs'


def test_registered_section_skill_is_called_with_contract(tmp_path):
    from bioflow_skills.core import BaseSkill, SkillCategory, SkillSpec
    from bioflow_skills.registry import get_registry
    seen = {}

    class DataRecords(BaseSkill):
        spec = SkillSpec(name='highlights-writer', version='2.0.0-test', category=SkillCategory.WRITING,
                         description='test skill')

        def run(self, skill_input):
            seen.update(skill_input.payload)
            n = next(f['text'] for f in skill_input.payload['ledger']['facts'] if f['id'] == 'COHORT.sample_count')
            return self.success(data={'markdown': f'Reads from {n} samples are deposited.', 'warnings': ['check']})

    registry = get_registry()
    registry.register(DataRecords)
    try:
        state = run(tmp_path, ['methods', 'results', 'highlights'])
    finally:
        registry.unregister('highlights-writer', '2.0.0-test')
    draft = state.drafts['highlights']
    assert draft.markdown.strip() == 'Reads from 3 samples are deposited.'
    assert draft.metadata['library']['status'] == 'written' and draft.warnings == ['check']
    assert seen['section'] == 'highlights' and 'methods' in seen['written'] and 'results' in seen['written']
    assert seen['brief']['title'] and seen['model'] is not None


def test_api_validates_and_lists_library():
    from fastapi.testclient import TestClient
    from frontend import server
    client = TestClient(server.app)
    data = client.get('/api/section-library').json()
    assert data['default_article_type'] == 'research_article'
    assert {t['id'] for t in data['article_types']} >= {'data_descriptor', 'research_article'}
    bad = client.post('/api/sessions', json={'sections': ['results', 'nope']})
    assert bad.status_code == 422 and '章节选择无效' in bad.json()['detail']


def test_latex_template_has_slots_for_library_sections():
    from frontend.latex_template import _KNOWN_SLOTS, _SLOT_TO_SECTION
    assert _SLOT_TO_SECTION['DATA_RECORDS'] == 'data_records' and 'TECHNICAL_VALIDATION' in _KNOWN_SLOTS
    assert _SLOT_TO_SECTION['BACKMATTER'] == 'back_matter'
