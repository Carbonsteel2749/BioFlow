import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'article_writing'))

from bioflow_skills import run_skill  # noqa: E402
from bioflow_skills.skills.writing.fact_ledger import build_ledger  # noqa: E402
from bioflow_skills.skills.writing.methods_provenance import AUTHOR_INPUT, build_methods  # noqa: E402


def package(tmp_path, *, provenance=True):
    task = tmp_path / 'task'
    root = task / 'nodes' / 'WF_REPORT_ABC'
    (root / 'tables').mkdir(parents=True)
    (root / 'tables/read_counts.tsv').write_text(
        'sample_id\traw_reads\tpost_fastp_reads\tpost_fastp_pairs\tpost_host_pairs\thost_removed_pairs\n'
        's1\t1000\t900\t450\t440\t10\n'
        's2\t3000\t2400\t1200\t1180\t20\n'
        's3\t2000\t1900\t950\t949\t1\n')
    header = 'sample_id\ttaxid\ttaxonomy\ttaxonomy_rank\tabundance\tabundance_unit\tclassification_source\n'
    rows = [('s1', 'Alpha one', 0.5), ('s1', 'Beta two', 0.5),
            ('s2', 'Alpha one', 0.9), ('s2', 'Gamma three', 0.1),
            ('s3', 'Beta two', 1.0)]
    (root / 'tables/species_abundance.tsv').write_text(header + ''.join(
        f'{s}\t1\t{t}\tspecies\t{v}\tfraction_of_reads\tKraken2+Bracken\n' for s, t, v in rows)
        + 's1\t9\tGenus x\tgenus\t0.9\tfraction_of_reads\tKraken2+Bracken\n')
    fastp = task / 'nodes' / 'WF_FASTP' / 'fastp'
    for sample, q30 in (('s1', 0.91), ('s2', 0.95), ('s3', 0.93)):
        (fastp / sample).mkdir(parents=True)
        (fastp / sample / f'{sample}.fastp.json').write_text(json.dumps({
            'summary': {'sequencing': 'paired end (150 cycles + 150 cycles)',
                        'before_filtering': {'q30_rate': q30 - 0.02},
                        'after_filtering': {'q30_rate': q30, 'gc_content': 0.47, 'read1_mean_length': 140}},
            'duplication': {'rate': 0.05}, 'insert_size': {'peak': 180}}))
    if provenance:
        prov = root / 'provenance'
        prov.mkdir()
        (prov / 'software_versions.tsv').write_text(
            'tool\tversion\nfastp\tfastp 1.3.6\nbowtie2\t[HOME] version 2.5.5\nnextflow\tN E X T F L O W\n')
        (prov / 'database_versions.tsv').write_text(
            'section\tdatabase_name\trelease\ttaxonomy_system\tmanifest_sha256\n')
        (prov / 'parameters.json').write_text(json.dumps({
            'fastp_cut_mean_quality': 20, 'fastp_cut_window_size': 4, 'fastp_cut_front': True,
            'fastp_cut_tail': True, 'fastp_length_required': 50, 'host_bowtie2_preset': 'very-sensitive',
            'host_index': '[HOME]'}))
    return root


def by_id(ledger):
    return {f['id']: f for f in ledger['facts']}


def test_ledger_computes_ranges_and_extremes_in_code(tmp_path):
    facts = by_id(build_ledger(package(tmp_path).parent.parent))
    assert facts['QC.raw_reads.max']['text'] == '3,000 (s2)'
    assert facts['QC.raw_reads.min']['text'] == '1,000 (s1)'
    assert facts['QC.raw_reads.range']['text'] == '1,000 to 3,000'
    assert facts['QC.raw_reads.total']['value'] == 6000
    assert facts['QC.post_host_pairs.s3']['text'] == '949'
    assert facts['QC.fastp_retained_pct.max']['text'] == '95.00% (s3)'
    assert facts['QC.host_removed_pct.min']['sample_id'] == 's3'
    assert facts['READQ.q30_after.range']['text'] == '91.00% to 95.00%'
    assert facts['READQ.sequencing_mode']['value'].startswith('paired end')


def test_ledger_taxonomy_and_diversity_use_species_rows_only(tmp_path):
    facts = by_id(build_ledger(package(tmp_path)))
    assert facts['TAX.top1.s2']['value'] == {'taxon': 'Alpha one', 'fraction': 0.9}
    # Genus rows never enter species summaries.
    assert all('Genus x' not in str(f['value']) for f in facts.values())
    assert facts['DIV.shannon.s1']['value'] == pytest.approx(math.log(2))
    assert facts['DIV.shannon.s3']['value'] == pytest.approx(0.0)
    assert facts['TAX.cohort_top.1']['value']['taxon'] == 'Beta two'
    assert facts['TAX.cohort_top.1']['value']['prevalence'] == 2
    assert 'Alpha one in 2 samples' in facts['TAX.dominant_summary']['text'] or \
           'Beta two in 2 samples' in facts['TAX.dominant_summary']['text']


def test_ledger_redacts_paths_and_notes_unparsed_versions(tmp_path):
    ledger = build_ledger(package(tmp_path))
    facts = by_id(ledger)
    assert facts['SW.bowtie2']['value'] == '2.5.5'
    assert 'SW.nextflow' not in facts
    assert any('nextflow' in note for note in ledger['notes'])
    assert 'PARAM.host_index' not in facts
    assert all('/home' not in str(f['value']) for f in ledger['facts'])


def test_methods_write_only_recorded_steps(tmp_path):
    methods = build_methods(build_ledger(package(tmp_path)))
    text = methods['markdown']
    assert 'fastp v1.3.6' in text and 'window size 4, mean quality 20' in text
    assert 'minimum read length of 50 bp' in text
    assert 'Bowtie2 v2.5.5 in very-sensitive mode' in text
    assert 'paired-end 2 × 150 bp reads' in text
    # Steps without recorded software are not described.
    assert 'Kraken2' not in text and 'HUMAnN' not in text and 'FastQC' not in text
    assert AUTHOR_INPUT in text and 'DNA extraction' in text
    quality = next(p for p in methods['paragraphs'] if p['key'] == 'quality')
    assert '1.3.6' in quality['keep_tokens'] and 'PARAM.fastp_cut_mean_quality' in quality['fact_ids']


def test_skills_are_registered_and_runnable(tmp_path):
    root = package(tmp_path)
    ledger = run_skill('bio-fact-ledger', {'package_root': str(root)})
    assert ledger.succeeded and ledger.data['sample_ids'] == ['s1', 's2', 's3']
    methods = run_skill('methods-from-provenance', {'ledger': ledger.data})
    assert methods.succeeded and methods.data['paragraphs']


def test_section_facts_give_summaries_not_per_sample_rows(tmp_path):
    from article_writing.contracts import SectionId
    from article_writing.skills import bio_skills
    ledger = bio_skills.build_ledger(package(tmp_path))
    results = bio_skills.section_facts(ledger, SectionId.results)
    assert 'Never compute a range' in results and 'No group comparison' in results
    assert '[QC.raw_reads.range] range of raw reads: 1,000 to 3,000' in results
    assert 'QC.raw_reads.s1' not in results and '.median]' not in results
    assert results.index('Block: Sequencing depth') < results.index('Block: Species composition')
    intro = bio_skills.section_facts(ledger, SectionId.introduction)
    assert 'QC.raw_reads.range' not in intro


def test_compose_retries_once_when_numbers_lack_ledger_source(tmp_path):
    from article_writing.contracts import PaperBrief, SectionDraft, SectionId
    from article_writing.llm.compose_from_catalog import compose_section_from_catalog
    from article_writing.skills import bio_skills
    ledger = bio_skills.build_ledger(package(tmp_path))
    catalog = [bio_skills.ledger_catalog_item(ledger)]
    replies = iter(['Raw reads ranged from 1,000 to 987,654 reads.',
                    'Raw reads ranged from 1,000 to 3,000 reads [QC.raw_reads.range].'])
    prompts = []

    class Model:
        def generate(self, prompt, **kwargs):
            prompts.append(prompt)
            return next(replies)

    draft = compose_section_from_catalog(
        SectionDraft(section=SectionId.results, title='Results', markdown=''), Model(),
        catalog=catalog, brief=PaperBrief(title='Test'), language='en', strict=True, translate=False,
        facts=bio_skills.section_facts(ledger, SectionId.results))
    assert len(prompts) == 2
    assert 'not in the facts: 987' in prompts[1]
    assert draft.metadata['number_retry']['first_pass_unsourced']
    assert '1,000 to 3,000' in draft.markdown and 'QC.raw_reads' not in draft.markdown


def test_methods_skill_output_has_no_unsourced_numbers(tmp_path):
    from article_writing.llm.compose_from_catalog import _numbers_not_in_catalog
    from article_writing.skills import bio_skills
    ledger = bio_skills.build_ledger(package(tmp_path))
    methods = bio_skills.build_methods(ledger)
    assert _numbers_not_in_catalog(methods['markdown'], [bio_skills.ledger_catalog_item(ledger)]) == []
