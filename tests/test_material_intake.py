import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'article_writing'))
from article_writing.adapters.live_analysis import LiveAnalysisPort
from article_writing.adapters.materials import inspect_materials, material_instructions
from frontend.analysis_packages import list_analysis_packages


def package(tmp_path):
    root = tmp_path / 'task' / 'nodes' / 'WF_REPORT_ABC'
    (root / 'tables').mkdir(parents=True)
    (root / 'tables/read_counts.tsv').write_text(
        'sample_id\traw_reads\tpost_fastp_reads\tpost_fastp_pairs\tpost_host_pairs\thost_removed_pairs\n'
        's1\t100\t80\t40\t35\t5\n'
        's2\t200\t160\t80\t70\t10\n')
    (root / 'tables/species_abundance.tsv').write_text(
        'sample_id\ttaxonomy\ttaxid\tabundance\tabundance_unit\n'
        's1\tSpecies A\t1\t0.2\tfraction_of_reads\n'
        's2\tSpecies B\t2\t0.8\tfraction_of_reads\n'
        's1\tSpecies C\t3\t0.4\tfraction_of_reads\n')
    (root / 'tables/gene_families.tsv').write_text('sample_id\tabundance\ns1\t100\n')
    return root


def test_node_package_streamed_and_source_linked(tmp_path, monkeypatch):
    root = package(tmp_path)
    # Legacy mapper and generic catalog must not load giant result tables.
    import article_writing.adapters.live_analysis as module
    def forbidden(*args):
        raise AssertionError('legacy whole-table loader used')
    monkeypatch.setattr(module, 'map_biollm_package', forbidden)
    monkeypatch.setattr(module, 'build_catalog', forbidden)
    port = LiveAnalysisPort(run_dir=str(tmp_path / 'task'))
    bundle = port.load()
    report = bundle.raw['materials']
    assert report['sample_count'] == 2
    assert port.package_root == root
    values = [f for f in report['facts'] if f['kind'] == 'descriptive_taxonomy']
    assert values[0]['value']['taxon'] == 'Species C'
    assert values[0]['source']['row'] == 4
    assert values[0]['unit'] == 'fraction_of_reads'
    assert report['mode'] == 'descriptive_draft'
    assert not report['figures']
    assert all(t['inspection'] == 'header_only' for t in report['tables'])
    assert 'read_pairs' in material_instructions(report, 'results')


def test_duplicate_samples_rejected(tmp_path):
    root = package(tmp_path)
    p = root / 'tables/read_counts.tsv'
    p.write_text(p.read_text().replace('s2', 's1'))
    with pytest.raises(ValueError, match='duplicate'):
        inspect_materials(root)


def test_nested_archive_discovery_and_old_layout(tmp_path):
    root = package(tmp_path)
    (root / 'deliverables').mkdir()
    (root / 'deliverables/task.tar.gz').write_bytes(b'archive')
    old = tmp_path / 'old/deliverables'
    old.mkdir(parents=True)
    (old / 'old.tar.gz').write_bytes(b'archive')
    assert {p['task_id'] for p in list_analysis_packages(tmp_path)} == {'task', 'old'}


def test_multiple_reports_not_silently_selected(tmp_path):
    root = package(tmp_path)
    other = root.parent / 'WF_REPORT_OTHER/tables'
    other.mkdir(parents=True)
    (other / 'read_counts.tsv').write_text('sample_id\nwrong\n')
    with pytest.raises(ValueError, match='Multiple report'):
        LiveAnalysisPort(run_dir=str(tmp_path / 'task')).load()


def test_generated_number_without_source_is_rejected(tmp_path):
    from article_writing.llm.compose_from_catalog import compose_section_from_catalog, CatalogComposeError
    from article_writing.contracts import SectionDraft, SectionId, PaperBrief
    root = package(tmp_path)
    bundle = LiveAnalysisPort(run_dir=str(root)).load()
    class Model:
        def generate(self, *args, **kwargs):
            return 'The study included 987654321 samples.'
    with pytest.raises(CatalogComposeError, match='lack material sources'):
        compose_section_from_catalog(SectionDraft(section=SectionId.results, title='Results', markdown=''),
            Model(), catalog=bundle.raw['archive_catalog'], brief=PaperBrief(title='Test'),
            language='en', strict=True, translate=False)


def test_method_prompt_carries_sources_and_descriptive_boundary(tmp_path):
    from article_writing.llm.compose_from_catalog import compose_section_from_catalog
    from article_writing.contracts import SectionDraft, SectionId, PaperBrief
    root = package(tmp_path)
    bundle = LiveAnalysisPort(run_dir=str(root)).load()
    captured = []
    class Model:
        def generate(self, prompt, **kwargs):
            captured.append(prompt)
            return 'Only descriptive analyses were available; study design requires confirmation.'
    compose_section_from_catalog(SectionDraft(section=SectionId.methods, title='Methods', markdown=''),
        Model(), catalog=bundle.raw['archive_catalog'], brief=PaperBrief(title='Test'),
        language='en', strict=True, translate=False,
        facts=material_instructions(bundle.raw['materials'], 'methods'))
    assert 'MATERIAL CONTRACT: descriptive draft only' in captured[0]
    assert 'statistical methods ONLY if explicitly supported' in captured[0]
