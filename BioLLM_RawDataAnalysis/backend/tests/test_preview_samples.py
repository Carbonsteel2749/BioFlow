import json
from pathlib import Path

from backend.tests.test_uploads_and_preview import make_client
from backend.app.services.previews import _sample_table


def case(tmp_path):
    client, settings = make_client(tmp_path)
    manifest = settings.input_root / 'samples.csv'
    manifest.write_text('sample_id,read1,read2\nS01,S01_R1.fq,S01_R2.fq\nS02,S02_R1.fq,S02_R2.fq\n')
    task = client.post('/api/tasks', json={'manifest_path': str(manifest)}).json()
    root = settings.state_root / 'outputs' / task['id']
    def write(relative, text):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    write('tables/species_abundance.tsv', 'sample_id\ttaxonomy\tabundance\tabundance_unit\nS01\tShared species\t0.9\tfraction_of_reads\nS02\tShared species\t0.2\tfraction_of_reads\nS02\tOnly second\t0.3\tfraction_of_reads\n')
    for sid, raw in [('S01', 100), ('S02', 60)]:
        write(f'fastp/{sid}/{sid}.fastp.json', json.dumps({'summary': {'before_filtering': {'total_reads': raw}, 'after_filtering': {'total_reads': raw-10}}}))
    for filename in ('read_ko.tsv','read_ec.tsv','read_pathabundance.tsv'):
        write('tables/'+filename, 'sample_id\tfunction_id\tabundance\nS01\tFIRST\t9\n')
        write('tables/'+filename+'.1', 'sample_id\tfunction_id\tabundance\nS02\tSECOND\t3\n')
    return client, f"/api/tasks/{task['id']}/preview", write, manifest


def test_default_preview_chooses_one_manifest_sample_not_mixed_ranking(tmp_path):
    client, url, _, _ = case(tmp_path)
    body = client.get(url).json()
    assert body['sample_ids'] == ['S01', 'S02']
    assert body['selected_sample_id'] == 'S01'
    assert body['taxonomy_top'] == [{'sample_id':'S01','name':'Shared species','abundance':90.0}]
    assert [q['sample_id'] for q in body['qc_summary']] == ['S01']


def test_switch_reads_second_sample_from_collision_files_for_every_tab(tmp_path):
    client, url, _, _ = case(tmp_path)
    body = client.get(url, params={'sample_id':'S02'}).json()
    assert body['selected_sample_id'] == 'S02'
    assert body['taxonomy_top'] == [
        {'sample_id':'S02','name':'Only second','abundance':30.0},
        {'sample_id':'S02','name':'Shared species','abundance':20.0}]
    assert [q['raw_reads'] for q in body['qc_summary']] == [60]
    for key in ('ko','ec','pathways'):
        assert body[key]['rows'] == [['S02','SECOND',3]]
        assert body[key]['total_rows'] == 1


def test_unknown_sample_never_falls_back_to_first(tmp_path):
    client, url, _, _ = case(tmp_path)
    assert client.get(url, params={'sample_id':'OTHER'}).status_code == 422
    assert client.get(url, params={'sample_id':'../../S01'}).status_code == 422


def test_missing_selected_table_is_not_replaced_by_other_sample(tmp_path):
    client, url, write, _ = case(tmp_path)
    write('tables/read_ec.tsv.1', 'sample_id\tfunction_id\tabundance\n')
    write('tables/read_ko.tsv.1', 'sample_id\tfunction_id\tabundance\n')
    write('tables/read_pathabundance.tsv.1', 'sample_id\tfunction_id\tabundance\n')
    body = client.get(url, params={'sample_id':'S02'}).json()
    assert body['ko'] is None and body['ec'] is None and body['pathways'] is None
    assert body['selected_sample_id'] == 'S02'


def test_unlabelled_cohort_table_is_not_assigned_to_selected_sample(tmp_path):
    client, url, write, _ = case(tmp_path)
    write('tables/read_ko.tsv', 'function_id\tabundance\nAMBIGUOUS\t999\n')
    body = client.get(url, params={'sample_id':'S01'}).json()
    assert body['ko'] is None


def test_scoped_canonical_source_wins_over_copies_and_preserves_numeric_sample_ids(tmp_path):
    client, url, write, manifest = case(tmp_path)
    write('functional_annotation/S02/read_ko.tsv', 'sample_id\tfunction_id\tabundance\nS02\tCANONICAL\t2\n')
    body = client.get(url, params={'sample_id':'S02'}).json()
    assert body['ko']['rows'] == [['S02','CANONICAL',2]]
    manifest.write_text('sample_id,read1,read2\n001,001_R1.fq,001_R2.fq\n')
    write('functional_annotation/001/read_ko.tsv', 'sample_id\tfunction_id\tabundance\n001\tK001\t2\n')
    assert client.get(url).json()['ko']['rows'][0][0] == '001'


def test_relative_output_root_is_supported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = Path('runtime/outputs/task/taxonomy/S01')
    root.mkdir(parents=True)
    (root / 'species_abundance.tsv').write_text('sample_id\ttaxonomy\tabundance\nS01\tSpecies\t0.2\n')
    data = _sample_table(Path('runtime/outputs/task'), ('species_abundance.tsv',), 'S01', False)
    assert data['records'][0]['sample_id'] == 'S01'


def test_scan_limit_does_not_claim_selected_sample_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.app.services.previews.MAX_SCANNED_ROWS', 2)
    (tmp_path / 'read_ko.tsv').write_text('sample_id\tfunction_id\nS01\tK1\nS01\tK2\nS02\tK3\n')
    data = _sample_table(tmp_path, ('read_ko.tsv',), 'S02', False)
    assert data['records'] == []
    assert data['truncated'] is True
    assert 'total_rows' not in data
    (tmp_path / 'read_ko.tsv.1').write_text('sample_id\tfunction_id\nS02\tK3\n')
    assert _sample_table(tmp_path, ('read_ko.tsv',), 'S02', False)['records'][0]['function_id'] == 'K3'


def test_packaged_normalized_folder_uses_original_row_sample_identity(tmp_path):
    sid = 'A' * 101
    folder = tmp_path / 'tables' / 'by_sample' / sid[:100]
    folder.mkdir(parents=True)
    (folder / 'read_ko.tsv').write_text(f'sample_id\tfunction_id\n{sid}\tK1\nOTHER\tK2\n')
    data = _sample_table(tmp_path, ('read_ko.tsv',), sid, False)
    assert data['records'] == [{'sample_id': sid, 'function_id': 'K1'}]
    (folder / 'read_ec.tsv').write_text('function_id\nAMBIGUOUS\n')
    assert _sample_table(tmp_path, ('read_ec.tsv',), sid[:100], False) is None
