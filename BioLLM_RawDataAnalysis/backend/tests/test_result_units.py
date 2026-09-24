import pytest

from backend.tests.test_preview_samples import case


@pytest.mark.parametrize('unit,value,want,display', [
    ('fraction_of_reads', '0.005', 0.5, 'percent_of_reads'),
    ('percent_of_reads', '0.5', 0.5, 'percent_of_reads'),
    ('percent_of_reads', '1', 1, 'percent_of_reads'),
    ('', '0.5', 0.5, 'unknown'),
    ('unverified', '0.5', 0.5, 'unknown'),
])
def test_taxonomy_conversion_uses_declared_unit_not_magnitude(tmp_path, unit, value, want, display):
    client, url, write, _ = case(tmp_path)
    write('taxonomy/S01/species_abundance.tsv', f'sample_id\ttaxonomy\tabundance\tabundance_unit\nS01\tSpecies\t{value}\t{unit}\n')
    body = client.get(url).json()
    assert body['taxonomy_top'][0]['abundance'] == want
    assert body['taxonomy_measurement']['display_unit'] == display
    assert body['taxonomy_measurement']['source_unit'] == unit


def test_mixed_taxonomy_units_are_not_ranked_together(tmp_path):
    client, url, write, _ = case(tmp_path)
    write('taxonomy/S01/species_abundance.tsv', 'sample_id\ttaxonomy\tabundance\tabundance_unit\nS01\tA\t0.5\tfraction_of_reads\nS01\tB\t0.5\tpercent_of_reads\n')
    body = client.get(url).json()
    assert body['taxonomy_top'] == []
    assert body['taxonomy_measurement']['status'] == 'conflict'


@pytest.mark.parametrize('unit', ['RPK', 'CPM', 'HUMAnN_reported', ''])
def test_functional_preview_and_interpretation_share_unit_evidence(tmp_path, unit):
    client, url, write, _ = case(tmp_path)
    tid = url.split('/')[3]
    client.app.state.repository.update_step(tid, 'functional_annotation', status='succeeded')
    write('functional_annotation/S01/read_ko.tsv', 'sample_id\tfunction_source\tfunction_namespace\tfunction_id\tfunction_name\tfunction_type\tabundance\tabundance_unit\tanalysis_scope\tnormalization_method\n' + f'S01\tHUMAnN\tKO\tK1\tK1\tko\t0.5\t{unit}\tcommunity_total\tprovided by source\n')
    data = client.get(url).json()['ko']
    summary = client.get(f'/api/tasks/{tid}/interpretation').json()
    section = next(s for s in summary['sections'] if s.get('function_type') == 'ko')
    assert data['rows'][0][6] == 0.5
    assert data['measurement'] == section['metrics']['measurement']
    assert data['measurement']['note'] in summary['text']
    assert summary['text'] in client.get(f'/api/tasks/{tid}/interpretation/download').text
    if unit in ('', 'HUMAnN_reported'):
        assert data['measurement']['status'] == 'unknown'
        assert '单位未确认' in summary['text']
        prompt = client.get(f'/api/tasks/{tid}/interpretation/prompt').json()
        assert '单位未确认' in prompt['user']
    if unit == 'CPM':
        assert '未经统一测序深度标准化' not in section['text']


def test_percent_taxonomy_has_same_preview_and_interpretation_value(tmp_path):
    client, url, write, _ = case(tmp_path)
    tid = url.split('/')[3]
    client.app.state.repository.update_step(tid, 'taxonomy', status='succeeded')
    write('taxonomy/S01/species_abundance.tsv', 'sample_id\ttaxid\ttaxonomy\ttaxonomy_rank\tabundance\tabundance_unit\nS01\t1\tSpecies\tspecies\t0.5\tpercent_of_reads\n')
    body = client.get(f'/api/tasks/{tid}/interpretation').json()
    section = next(s for s in body['sections'] if s['category'] == 'taxonomy')
    assert section['metrics']['top_taxa'][0]['fraction'] == 0.005
    assert '0.50%' in section['text']
    assert section['metrics']['measurement'] == client.get(url).json()['taxonomy_measurement']


def test_grouped_functional_notes_identify_each_layers_unit(tmp_path):
    client, url, write, _ = case(tmp_path)
    tid = url.split('/')[3]
    client.app.state.repository.update_step(tid, 'functional_annotation', status='succeeded')
    for kind, unit in [('ko', 'RPK'), ('ec', 'CPM')]:
        write(f'functional_annotation/S01/read_{kind}.tsv', 'sample_id\tfunction_source\tfunction_namespace\tfunction_id\tfunction_name\tfunction_type\tabundance\tabundance_unit\tanalysis_scope\n' + f'S01\tHUMAnN\t{kind.upper()}\tF1\tF1\t{kind}\t0.5\t{unit}\tcommunity_total\n')
    body = client.get(f'/api/tasks/{tid}/interpretation').json()
    group = next(s for s in body['display_sections'] if s['category'] == 'functional')
    assert 'KO：RPK' in group['common_notes']
    assert 'EC：CPM' in group['common_notes']
    assert group['common_notes'] in client.get(f'/api/tasks/{tid}/interpretation/download').text
