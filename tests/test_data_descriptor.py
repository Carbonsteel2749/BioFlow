import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'article_writing'))
sys.path.insert(0, str(ROOT / 'tests'))

from bioflow_skills import run_skill  # noqa: E402
from bioflow_skills.skills.writing.fact_ledger import build_ledger  # noqa: E402
from bioflow_skills.skills.writing.section_planner import attach_figures, build_plan  # noqa: E402
from test_writing_core_skills import package  # noqa: E402


def payload(tmp_path, **extra):
    from article_writing.adapters.materials import inspect_materials
    root = package(tmp_path)
    (root / 'tables/species_abundance.tsv').exists()
    base = {'ledger': build_ledger(root), 'materials': inspect_materials(root),
            'brief': {'title': 'Stool metagenomes from ENA project PRJEB1234', 'research_question': ''}}
    base.update(extra)
    return base


def test_data_records_restate_inventory_and_accessions(tmp_path):
    data = run_skill('data-records-writer', payload(tmp_path)).data
    text = data['markdown']
    assert 'European Nucleotide Archive (ENA) under project accession PRJEB1234' in text
    assert 'run accessions s1, s2, s3' in text
    assert 'read_counts.tsv' in text and 'species_abundance.tsv' in text
    assert 'The file read_counts.tsv (' in text and 'Read_counts' not in text
    assert 'the software versions (software_versions.tsv)' in text and 'checksums' not in text
    assert 'AUTHOR_INPUT_NEEDED: repository and accession or DOI' in text
    assert data['warnings'] == []


def test_data_records_without_accession_asks_author(tmp_path):
    data = run_skill('data-records-writer', payload(tmp_path, brief={'title': 'Stool metagenomes'})).data
    assert 'AUTHOR_INPUT_NEEDED: archive and project accession' in data['markdown']
    assert data['warnings']


def test_statement_sections_never_invent_locations(tmp_path):
    p = payload(tmp_path)
    usage = run_skill('usage-notes-writer', p).data['markdown']
    assert 'fractions of classified reads' in usage and 'no participant metadata' in usage
    code = run_skill('code-availability-writer', p).data['markdown']
    assert 'fastp v1.3.6' in code and 'AUTHOR_INPUT_NEEDED: public repository URL' in code
    avail = run_skill('data-availability-writer', p).data['markdown']
    assert 'PRJEB1234' in avail and 'AUTHOR_INPUT_NEEDED' in avail


def figure(fid, chart, caption='A caption.'):
    return {'figure_id': fid, 'path': f'/x/{fid}.png', 'chart': chart, 'caption': caption}


def test_figures_go_to_matching_paragraph_and_are_numbered_in_reading_order(tmp_path):
    ledger = build_ledger(package(tmp_path))
    plan = build_plan(ledger, 'technical_validation', {}, [],
                      [figure('fig_b', 'shannon'), figure('fig_a', 'pca'), figure('fig_c', 'mystery')])
    by_key = {p['key']: p for p in plan}
    assert [f['figure_id'] for f in by_key['composition']['figures']] == ['fig_a']
    assert by_key['composition']['figures'][0]['number'] == 1
    assert by_key['diversity']['figures'][0]['number'] == 2
    assert plan[-1]['figures'][-1]['figure_id'] == 'fig_c'
    assert '![A caption.](figures/fig_a.png)' in by_key['composition']['figures'][0]['embed']
    assert any('(Figure 1)' in rule for rule in by_key['composition']['forbid'])
    assert any(line.startswith('Figure 1 shows') for line in by_key['composition']['facts'])
    assert all('hypothesis' in ' '.join(p['forbid']) for p in plan)
    assert attach_figures([], [figure('x', 'pca')]) == []


class Model:
    enabled = True

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, system=None):
        self.prompts.append(prompt)
        if 'Figure 1 shows' in prompt:
            return 'The dominant species are shown in Figure 1 (Figure 1).'
        return 'The samples were described with the listed facts.'

    def generate_json(self, prompt, system, schema):
        return {'sentences': []}


def test_technical_validation_skill_embeds_figures(tmp_path):
    model = Model()
    data = run_skill('technical-validation-writer',
                     payload(tmp_path, model=model, figures=[figure('fig_a', 'pca', 'PCA of the samples.')])).data
    text = data['markdown']
    assert '(Figure 1)' in text and '![PCA of the samples.](figures/fig_a.png)' in text
    assert '**Figure 1.** PCA of the samples.' in text
    assert data['figures'] == [{'figure_id': 'fig_a', 'number': 1}]
    assert text.index('(Figure 1)') < text.index('![PCA')


def test_background_summary_without_literature_does_not_cite(tmp_path):
    data = run_skill('background-summary-writer', payload(tmp_path, model=Model())).data
    assert data['markdown'].strip() and data['citations'] == []


def make_figure_db(tmp_path, rows):
    runtime = tmp_path / 'platform' / 'runtime'
    runtime.mkdir(parents=True)
    db = runtime / 'bioflow.sqlite3'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE project_figures(id TEXT, project_id TEXT, user_id TEXT, chart TEXT, status TEXT,'
                    ' parameters TEXT, source_json TEXT, parent_id TEXT, result TEXT, error TEXT, saved INTEGER,'
                    ' created_at TEXT)')
        for fid, chart, source, content, sha in rows:
            folder = runtime / 'figures' / 'u1' / 'p1' / fid
            folder.mkdir(parents=True)
            (folder / 'figure.png').write_bytes(content)
            result = {'outputs': {'png': {'sha256': sha or hashlib.sha256(content).hexdigest()}}}
            con.execute('INSERT INTO project_figures VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                        (fid, 'p1', 'u1', chart, 'completed', '{"title":"t"}', json.dumps(source), None,
                         json.dumps(result), None, 1, 'now'))
    return db


def test_resolve_figures_refuses_examples_and_bad_checksums(tmp_path):
    from article_writing.skills.figures import resolve_figures
    db = make_figure_db(tmp_path, [
        ('aaaaaaaaaaaaaaaa', 'pca', {'name': 'family_pca.tsv', 'task_id': 't1'}, b'png-a', None),
        ('bbbbbbbbbbbbbbbb', 'stacked', {'id': 'example:stacked', 'simulated': True}, b'png-b', None),
        ('cccccccccccccccc', 'heatmap', {'name': 'h.tsv'}, b'png-c', 'deadbeef'),
    ])
    figures, warnings = resolve_figures(['aaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbb', 'cccccccccccccccc', 'zzz'], db)
    assert [f['source_figure'] for f in figures] == ['aaaaaaaaaaaaaaaa']
    assert figures[0]['figure_id'] == 'fig_aaaaaaaaaaaa'
    assert figures[0]['caption'].startswith('Principal component analysis') and 'family_pca.tsv' in figures[0]['caption']
    assert any('example data' in w for w in warnings) and any('checksum' in w for w in warnings)
    assert any('not found' in w for w in warnings)


def test_data_descriptor_pipeline_with_figures_and_references(tmp_path, monkeypatch):
    from article_writing.orchestrator.pipeline import WritingPipeline
    db = make_figure_db(tmp_path, [('aaaaaaaaaaaaaaaa', 'pca', {'name': 'family_pca.tsv'}, b'png-a', None)])
    monkeypatch.setenv('ARTICLE_WRITING_FIGURE_DB', str(db))
    from article_writing import section_library
    order = section_library.resolve_sections('data_descriptor')
    order = [s for s in order if s != 'back_matter']
    pipe = WritingPipeline(fixtures_dir=ROOT / 'article_writing/fixtures', adapter_mode='live',
                           analysis_run_dir=str(package(tmp_path)), literature_enabled=False, react_enabled=False,
                           brief_text='Stool metagenomes from ENA project PRJEB1234', llm=Model(), llm_language='en',
                           section_order=order, figure_ids=['aaaaaaaaaaaaaaaa'])
    state = pipe.run(run_id='dd')
    for name in ('background_summary', 'data_records', 'technical_validation', 'usage_notes', 'code_availability'):
        assert state.drafts[name].metadata['library']['status'] == 'written', name
    tv = state.drafts['technical_validation']
    assert [r.figure_id for r in tv.figure_refs] == ['fig_aaaaaaaaaaaa']
    assert 'fig_aaaaaaaaaaaa' in {r.figure_id for r in state.figure_refs}
    assert 'PRJEB1234' in state.drafts['data_records'].markdown


def test_inline_figures_stay_in_place_once_per_language(tmp_path):
    from article_writing.layout.bilingual import select_language
    from frontend.latex_preview import markdown_to_latex
    md = ('First paragraph.\n\n![Cap.](figures/fig_a.png)\n\n**Figure 1.** Cap.\n\nSecond paragraph.\n\n中文\n\n'
          '第一段。\n\n![Cap.](figures/fig_a.png)\n\n**Figure 1.** Cap.\n\n第二段。')
    en = select_language(md, 'en')
    assert en.count('![Cap.]') == 1 and en.index('First') < en.index('![Cap.]') < en.index('Second')
    zh = select_language(md, 'zh')
    assert zh.count('![Cap.]') == 1 and zh.index('第一段') < zh.index('![Cap.]') < zh.index('第二段')
    (tmp_path / 'fig_a.png').write_bytes(b'png')
    assert markdown_to_latex(en, tmp_path).count('includegraphics') == 1
    # trailing (legacy) embeds that appear once are still shared by both languages
    legacy = 'Text.\n\n中文\n\n文字。\n\n![Cap.](figures/fig_a.png)\n\n**Figure 1.** Cap.'
    assert '![Cap.]' in select_language(legacy, 'en') and '![Cap.]' in select_language(legacy, 'zh')


def test_translation_keeps_figure_lines():
    from article_writing.llm.compose_from_catalog import translate_section_text

    class Translator:
        def generate(self, prompt, system=None):
            assert '![' not in prompt and '**Figure 1.**' not in prompt
            return '物种组成见图（Figure 1）。 CITEREF1 CITEREF2 多样性较高。'

    text = ('Species composition is shown in Figure 1 (Figure 1).\n\n![PCA.](figures/fig_a.png)\n\n'
            '**Figure 1.** PCA.\n\nDiversity was high.')
    chinese = translate_section_text(Translator(), text, 'en+zh').split('\n中文\n')[1]
    assert '\n\n![PCA.](figures/fig_a.png)\n\n' in chinese and '\n\n**Figure 1.** PCA.' in chinese
