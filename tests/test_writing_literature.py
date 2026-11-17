import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'article_writing'))
sys.path.insert(0, str(ROOT / 'tests'))

from bioflow_skills.skills.literature.reference_cards import build_cards, normalise_doi  # noqa: E402
from bioflow_skills.skills.writing.claim_verifier import verify  # noqa: E402
from bioflow_skills.skills.writing.fact_ledger import build_ledger  # noqa: E402
from bioflow_skills.skills.writing.section_planner import build_plan  # noqa: E402
from test_writing_core_skills import package  # noqa: E402

RECORDS = {
    '10.1000/gut.1': {
        'doi': '10.1000/gut.1', 'title': 'Gut microbes and behaviour', 'authors': ['Zhong Z', 'Lin C', 'Cao Y'],
        'year': '2026', 'journal': 'Brain Behav Immun', 'pmid': '1', 'source': 'test',
        'abstract': ('Autism is frequently associated with gastrointestinal dysfunction. '
                     'Here, we show in a mouse model that Alpha one restores gut barrier integrity. '
                     'The effect required cholesterol synthesis.'),
    },
    '10.1000/gut.2': {
        'doi': '10.1000/gut.2', 'title': 'Enteric nervous system review', 'authors': ['Zhong A'],
        'year': '2026', 'journal': 'Autism Res', 'pmid': '2', 'source': 'test',
        'abstract': 'Gastrointestinal disorders are frequently reported in autism. The enteric nervous system controls motility.',
    },
}


def fake_fetch(doi):
    return RECORDS.get(doi)


class ClaimModel:
    enabled = True

    def generate_json(self, prompt, system, schema):
        if 'Gut microbes' in prompt:
            return {'claims': [
                {'text': 'Autism is frequently associated with gastrointestinal dysfunction.', 'sentences': [0],
                 'kind': 'background'},
                {'text': 'Alpha one restores gut barrier integrity in a mouse model.', 'sentences': [1],
                 'kind': 'finding'},
                # Hallucinated: no overlap with the cited sentence, so it must be dropped.
                {'text': 'Probiotics cure depression in humans.', 'sentences': [2], 'kind': 'finding'},
                {'text': 'Out of range sentence.', 'sentences': [9], 'kind': 'finding'},
            ]}
        return {'claims': [{'text': 'Gastrointestinal disorders are frequently reported in autism.',
                            'sentences': [0], 'kind': 'background'}]}


def cards_for(tmp_path, model=ClaimModel()):
    refs = [{'doi': 'https://doi.org/10.1000/GUT.1'}, {'doi': 'doi:10.1000/gut.2'}, {'doi': '10.1000/gut.1'},
            {'title': 'No DOI paper'}]
    return build_cards(refs, model=model, cache_dir=tmp_path / 'cache', fetchers=[fake_fetch])


def test_cards_fetch_dedupe_key_and_drop_unfaithful_claims(tmp_path):
    data = cards_for(tmp_path)
    keys = [c['key'] for c in data['cards']]
    assert keys == ['Zhong2026', 'Zhong2026a']
    assert data['skipped'] == [{'title': 'No DOI paper', 'reason': 'no DOI'}]
    first = data['cards'][0]
    texts = [c['text'] for c in first['claims']]
    assert 'Probiotics cure depression in humans.' not in texts and len(texts) == 2
    assert first['claims'][1]['quote'].startswith('Here, we show in a mouse model')
    assert first['claims'][0]['id'] == 'Zhong2026.C1'
    assert normalise_doi('https://dx.doi.org/10.1000/ABC') == '10.1000/abc'


def test_cards_are_cached_and_work_offline(tmp_path):
    cards_for(tmp_path)
    offline = build_cards([{'doi': '10.1000/gut.2'}], cache_dir=tmp_path / 'cache', offline=True, fetchers=[])
    assert offline['cards'][0]['title'] == 'Enteric nervous system review'
    # Without a model the first abstract sentences are used verbatim.
    assert offline['cards'][0]['claims'][0]['text'].startswith('Gastrointestinal disorders')


def test_introduction_plan_follows_cars_and_requires_citations(tmp_path):
    cards = cards_for(tmp_path)['cards']
    plan = build_plan(build_ledger(package(tmp_path)), 'introduction', {'title': 'T'}, cards)
    assert [p['key'] for p in plan] == ['background', 'open_questions', 'aim']
    background = plan[0]
    assert background['require_citations'] and set(background['citations']) == {'Zhong2026', 'Zhong2026a'}
    assert all(f.startswith('[@') for f in background['facts'])
    assert any('mouse' in n for n in plan[1]['notes'])
    assert not plan[2]['citations'] and any('diagnosis' in rule for rule in plan[2]['forbid'])


def test_discussion_gets_literature_paragraph_with_shared_species(tmp_path):
    cards = cards_for(tmp_path)['cards']
    plan = build_plan(build_ledger(package(tmp_path)), 'discussion', {}, cards)
    keys = [p['key'] for p in plan]
    assert keys.index('literature') == keys.index('limitations') - 1
    literature = plan[keys.index('literature')]
    assert any('Alpha one' in n and '[@Zhong2026]' in n for n in literature['notes'])
    assert any('cannot test or confirm' in n for n in literature['notes'])


def test_citation_checks(tmp_path):
    cards = cards_for(tmp_path)['cards']
    ledger = build_ledger(package(tmp_path))
    background = build_plan(ledger, 'introduction', {}, cards)[0]
    good = ('Autism is frequently associated with gastrointestinal dysfunction [@Zhong2026]. '
            'Gastrointestinal disorders are frequently reported in autism [@Zhong2026a].')
    assert verify(good, background)['ok']
    uncited = 'Autism is frequently associated with gastrointestinal dysfunction.'
    assert any('without a citation' in i for i in verify(uncited, background)['issues'])
    unknown = 'Gut barrier integrity matters [@Smith2020].'
    assert any('[@Smith2020]' in i for i in verify(unknown, background)['issues'])
    named = 'Zhong et al. reported gut changes [@Zhong2026].'
    assert any('names authors' in i for i in verify(named, background)['issues'])
    exempt = 'In this study, we profiled the samples.'
    assert not any('without a citation' in i for i in verify(exempt, background)['issues'])
    results = build_plan(ledger, 'results', {}, cards)[0]
    assert any('not a reference' in i for i in verify('Depth varied [@Zhong2026].', results)['issues'])


def test_references_block_is_added_to_both_languages():
    from article_writing.skills import bio_skills
    cards = [{'key': 'Zhong2026', 'authors': ['Zhong Z', 'Lin C'], 'title': 'Gut microbes', 'journal': 'BBI',
              'year': '2026', 'doi': '10.1/x'}]
    block = bio_skills.references_block(cards, ['Zhong2026', 'Missing2020'])
    assert block == 'References\n\n[@Zhong2026]: Zhong Z, Lin C. Gut microbes. BBI. 2026. https://doi.org/10.1/x'
    merged = bio_skills.add_references('English text.\n\n中文\n\n中文正文。', block)
    assert merged.count('[@Zhong2026]:') == 2 and merged.index('中文\n') > merged.index('[@Zhong2026]:')


def test_translation_keeps_citation_markers():
    from article_writing.llm.compose_from_catalog import translate_section_text

    class Translator:
        def generate(self, prompt, system=None):
            assert '[@' not in prompt and 'CITEREF1' in prompt and 'CITEREF2' in prompt
            return '肠道功能障碍与自闭症相关 CITEREF1。胃肠道疾病常见 CITEREF2。'

    text = ('Autism is frequently associated with gastrointestinal dysfunction [@Zhong2026]. '
            'Gastrointestinal disorders are common [@Zhong2026a; @Zhong2026].')
    out = translate_section_text(Translator(), text, 'en+zh')
    chinese = out.split('\n中文\n')[1]
    assert '相关[@Zhong2026]' in chinese and '[@Zhong2026a; @Zhong2026]' in chinese


def test_latex_turns_markers_into_cite_and_bibliography(tmp_path):
    from frontend.latex_preview import markdown_to_latex, session_to_latex
    from frontend.session_store import SectionState, WritingSession
    body = markdown_to_latex('Known 50% effect [@Zhong_2026; @Lin2025].\n', tmp_path)
    assert r'\cite{Zhong_2026,Lin2025}' in body and r'50\%' in body
    back = ('Data are available.\n\nReferences\n\n[@Zhong_2026]: Zhong Z. Gut 100% microbes. BBI. 2026.\n\n'
            '[@Lin2025]: Lin C. Review. 2025.\n')
    session = WritingSession(
        session_id='abc', run_id='ui-abc', title='Test', research_question='Q',
        sections={'introduction': SectionState('introduction', 'Introduction', '引言', 'Known [@Zhong_2026].'),
                  'back_matter': SectionState('back_matter', 'Back matter', '文后', back)},
        section_order=['introduction', 'back_matter'], output_dir=str(tmp_path))
    tex = session_to_latex(session, 'en')
    assert r'\cite{Zhong_2026}' in tex
    assert r'\bibitem{Zhong_2026} Zhong Z. Gut 100\% microbes. BBI. 2026.' in tex
    assert tex.index(r'\bibitem{Zhong_2026}') < tex.index(r'\bibitem{Lin2025}') < tex.index(r'\end{document}')
    assert '[@Lin2025]:' not in tex and 'References' not in tex.split(r'\begin{thebibliography}')[0]


@pytest.mark.skipif(not shutil.which('xelatex'), reason='xelatex not installed')
def test_latex_with_citations_compiles_twice_to_pdf(tmp_path):
    from frontend.latex_preview import compile_latex, session_to_latex
    from frontend.session_store import SectionState, WritingSession
    back = 'References\n\n[@Zhong2026]: Zhong Z. Gut microbes. BBI. 2026.\n'
    session = WritingSession(
        session_id='abc', run_id='ui-abc', title='Test', research_question='Q',
        sections={'introduction': SectionState('introduction', 'Introduction', '引言', 'Known [@Zhong2026].'),
                  'back_matter': SectionState('back_matter', 'Back matter', '文后', back)},
        section_order=['introduction', 'back_matter'], output_dir=str(tmp_path))
    pdf, error = compile_latex(session, session_to_latex(session, 'en'))
    assert pdf is not None, error
    log = (tmp_path / 'pipeline' / 'manuscript.log').read_text(errors='replace')
    assert 'Citation `Zhong2026\' on page' not in log  # resolved after the second pass


def test_pipeline_cites_selected_papers_and_lists_references(tmp_path, monkeypatch):
    from article_writing.orchestrator.pipeline import WritingPipeline
    from article_writing.skills import bio_skills
    monkeypatch.setattr(bio_skills, 'build_reference_cards',
                        lambda refs, model=None, cache_dir=None: build_cards(
                            refs, model=ClaimModel(), cache_dir=tmp_path / 'c', fetchers=[fake_fetch]))

    class Model:
        enabled = True

        def generate(self, prompt, system=None):
            if 'Section: introduction' in prompt and '[@Zhong2026]' in prompt:
                return 'Autism is frequently associated with gastrointestinal dysfunction [@Zhong2026].'
            return 'In this study, the samples were described with the listed facts.'

        def generate_json(self, prompt, system, schema):
            return {'sentences': []}

    pipe = WritingPipeline(fixtures_dir=ROOT / 'article_writing/fixtures', adapter_mode='live',
                           analysis_run_dir=str(package(tmp_path)), literature_enabled=False, react_enabled=False,
                           brief_text='Describe gut profiles', llm=Model(), llm_language='en',
                           literature_refs=[{'doi': '10.1000/gut.1'}, {'doi': '10.1000/gut.2'}])
    state = pipe.run(run_id='lit')
    intro = state.drafts['introduction']
    assert '[@Zhong2026]' in intro.markdown
    assert intro.metadata['planned']['citations'] == ['Zhong2026']
    back = state.drafts['back_matter']
    assert '[@Zhong2026]: Zhong Z, Lin C, Cao Y. Gut microbes and behaviour' in back.markdown
    assert '[@Zhong2026a]:' not in back.markdown  # never cited, so not listed
    assert [c.cite_id for c in back.citations][-1] == 'Zhong2026'
