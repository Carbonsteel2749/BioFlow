import json
import pytest


def test_functional_display_groups_by_sample_orders_layers_and_deduplicates_notes(tmp_path):
    client, settings = make_client(tmp_path)
    manifest = settings.input_root / 'samples.csv'
    manifest.write_text('sample_id,read1,read2\nS1,a,b\nS2,c,d\n')
    with client:
        task = client.post('/api/tasks', json={'manifest_path': str(manifest)}).json()['id']
        client.app.state.repository.update_step(task, 'functional_annotation', status='succeeded')
        root = settings.state_root / 'outputs' / task / 'functional_annotation'
        header = 'sample_id\tfunction_source\tfunction_namespace\tfunction_id\tfunction_name\tfunction_type\tabundance\tabundance_unit\tanalysis_scope\n'
        for sample in ['S1', 'S2']:
            folder = root / sample; folder.mkdir(parents=True)
            for filename, kind, namespace in [('read_ec.tsv','ec','EC'), ('read_ko.tsv','ko','KO'), ('read_genefamilies.tsv','gene_family','UniRef90'), ('read_pathabundance.tsv','pathway_abundance','MetaCyc'), ('read_pathcoverage.tsv','pathway_coverage','MetaCyc')]:
                (folder / filename).write_text(header + f'{sample}\tHUMAnN\t{namespace}\t{sample}_F1\tFeature\t{kind}\t0.5\tHUMAnN_reported\tcommunity_total\n')
        body = client.get(f'/api/tasks/{task}/interpretation').json()
        groups = [s for s in body.get('display_sections', []) if s['category'] == 'functional']
        assert len(groups) == 2
        assert {s['sample_id'] for s in groups} == {'S1', 'S2'}
        for group in groups:
            assert [p['title'] for p in group['paragraphs']] == ['基因家族', 'KO', 'EC', '通路丰度与覆盖度']
            assert len(group['source_ids']) == len(set(group['source_ids'])) == 5
            assert group['text'].count('单位与解释边界') == 1
            assert group['text'].count('不能确认是否经过测序深度标准化') == 1
            assert ('S2_F1' if group['sample_id'] == 'S1' else 'S1_F1') not in group['text']
        assert body['text'].count('功能潜力 · S1') == 1
        exported = client.get(f'/api/tasks/{task}/interpretation/download').text
        assert body['text'] in exported
from backend.tests.test_uploads_and_preview import make_client


def test_interpretation_uses_full_per_sample_tables_and_separates_unknowns(tmp_path):
    client, settings = make_client(tmp_path)
    manifest = settings.input_root / 'samples.csv'
    manifest.write_text('sample_id,read1,read2\nS1,a,b\nS2,c,d\n')
    with client:
        task = client.post('/api/tasks', json={'manifest_path': str(manifest)}).json()['id']
        repo = client.app.state.repository
        repo.update_task(task, status='paused')
        repo.update_step(task, 'taxonomy', status='succeeded')
        repo.update_step(task, 'functional_annotation', status='succeeded')
        root = settings.state_root / 'outputs' / task
        for sample, name in [('S1','Species A'), ('S2','Species B')]:
            taxonomy = root / 'taxonomy' / sample / 'species_abundance.tsv'
            taxonomy.parent.mkdir(parents=True)
            taxonomy.write_text('sample_id\ttaxid\ttaxonomy\ttaxonomy_rank\tabundance\tabundance_unit\n'+f'{sample}\t1\t{name}\tspecies\t0.25\tfraction_of_reads\n')
            ko = root / 'functional_annotation' / sample / 'read_ko.tsv'
            ko.parent.mkdir(parents=True)
            header = 'sample_id\tfunction_source\tfunction_namespace\tfunction_id\tfunction_name\tfunction_type\tabundance\tabundance_unit\tanalysis_scope\n'
            ko.write_text(header+f'{sample}\tHUMAnN\tKO\tUNMAPPED\tUNMAPPED\tko\t999\tHUMAnN_reported\tcommunity_total\n'+f'{sample}\tHUMAnN\tKO\tK00001\tExample enzyme\tko\t2\tHUMAnN_reported\tcommunity_total\n')
        response = client.get(f'/api/tasks/{task}/interpretation')
        assert response.status_code == 200
        body = response.json()
        assert body['partial'] is True
        assert body['sample_count'] == 2
        sections = body['sections']
        assert {s['sample_id'] for s in sections if s['category'] == 'taxonomy'} == {'S1','S2'}
        functions = [s for s in sections if s['category'] == 'functional']
        assert len(functions) == 2
        assert all(s['metrics']['top_features'][0]['id'] == 'K00001' for s in functions)
        assert functions[0]['metrics']['special_entries']['UNMAPPED'] == 999
        assert all(s['source_ids'] for s in sections)
        assert '不代表基因表达或代谢活性' in body['text']
        assert '25.00%' in body['text']
        assert len(body['sources']) == 4
        assert all(len(s['sha256']) == 64 and not s['path'].startswith('/') for s in body['sources'])
        export = client.get(f'/api/tasks/{task}/interpretation/download')
        assert export.status_code == 200 and 'attachment' in export.headers['content-disposition']


def test_reasoning_adds_verified_metrics_and_evidence_linked_interpretation(tmp_path):
    client, settings = make_client(tmp_path)
    manifest = settings.input_root / 'samples.csv'; manifest.write_text('sample_id,read1,read2\nS1,a,b\n')
    with client:
        task = client.post('/api/tasks',json={'manifest_path':str(manifest)}).json()['id']
        repo = client.app.state.repository
        for stage in ['fastp','taxonomy','functional_annotation']: repo.update_step(task,stage,status='succeeded')
        root = settings.state_root/'outputs'/task
        qc=root/'fastp'/'S1.fastp.json'; qc.parent.mkdir(parents=True)
        qc.write_text(json.dumps({'summary':{'before_filtering':{'total_reads':100,'q30_rate':.8},'after_filtering':{'total_reads':90,'q30_rate':.9,'total_bases':9000}},'filtering_result':{'low_quality_reads':6,'too_short_reads':4}}))
        tax=root/'taxonomy'/'S1'/'species_abundance.tsv'; tax.parent.mkdir(parents=True)
        tax.write_text('sample_id\ttaxid\ttaxonomy\ttaxonomy_rank\tabundance\tabundance_unit\nS1\t1\tA\tspecies\t0.6\tfraction_of_reads\nS1\t2\tB\tspecies\t0.4\tfraction_of_reads\n')
        (tax.parent/'kraken.report').write_text('20\t2\t2\tU\t0\tunclassified\n80\t8\t0\tR\t1\troot\n')
        fun=root/'functional_annotation'/'S1';fun.mkdir(parents=True)
        header='sample_id\tfunction_source\tfunction_namespace\tfunction_id\tfunction_name\tfunction_type\tabundance\tabundance_unit\tanalysis_scope\n'
        (fun/'read_ko.tsv').write_text(header+'S1\tHUMAnN\tKO\tK1\tK1\tko\t2\tHUMAnN_reported\tcommunity_total\n')
        for suffix,kind in [('pathabundance','pathway_abundance'),('pathcoverage','pathway_coverage')]:
            (fun/f'read_{suffix}.tsv').write_text(header+f'S1\tHUMAnN\tMetaCyc\tUNMAPPED\tUNMAPPED\t{kind}\t0\tHUMAnN_reported\tcommunity_total\n')
        body=client.get(f'/api/tasks/{task}/interpretation').json()
        metrics={s['category']:s['metrics'] for s in body['sections'] if s['category']!='functional'}
        assert metrics['qc'].get('q30_after_pct') == 90
        assert metrics['qc'].get('q30_change_pp') == pytest.approx(10)
        assert metrics['qc']['filter_reasons']['low_quality_reads'] == 6
        assert metrics['taxonomy']['top3_fraction_pct'] == 100
        assert metrics['taxonomy']['shannon_observed'] == pytest.approx(0.673011667)
        assert metrics['taxonomy']['kraken_classified_pct'] == 80
        display={s['category']:s for s in body['display_sections']}
        assert '通路层面' in display['functional']['reasoning']['interpretation']
        assert '映射' in display['functional']['reasoning']['recommendation']
        for section in body['display_sections']:
            assert set(section['reasoning']) >= {'finding','interpretation','limitation','recommendation'}
            assert section['source_ids']
        assert '主要发现' in body['text'] and '建议关注' in body['text']
        prompt=client.get(f'/api/tasks/{task}/interpretation/prompt')
        assert prompt.status_code == 200
        assert '主要发现' in prompt.json()['system']
        assert 'raw_reads' not in prompt.json()['user']  # only bounded evidence narratives, not raw data
        assert str(settings.state_root) not in prompt.json()['user']


def test_optional_invalid_qc_fields_do_not_turn_into_false_quality_claims(tmp_path):
    client,settings=make_client(tmp_path)
    manifest=settings.input_root/'samples.csv';manifest.write_text('sample_id,read1,read2\nS1,a,b\n')
    with client:
        task=client.post('/api/tasks',json={'manifest_path':str(manifest)}).json()['id']
        client.app.state.repository.update_step(task,'fastp',status='succeeded')
        file=settings.state_root/'outputs'/task/'fastp'/'S1.fastp.json';file.parent.mkdir(parents=True)
        file.write_text(json.dumps({'summary':{'before_filtering':{'total_reads':100,'q30_rate':1.5},'after_filtering':{'total_reads':90,'q30_rate':True}},'filtering_result':{'low_quality_reads':99}}))
        body=client.get(f'/api/tasks/{task}/interpretation').json()
        m=body['sections'][0]['metrics']
        assert m.get('q30_after_pct') is None and m.get('q30_before_pct') is None
        assert m.get('filter_reasons') == {}
        assert 'Q30' in body['display_sections'][0]['reasoning']['limitation']


def test_incomplete_or_invalid_data_is_not_interpreted_as_zero(tmp_path):
    client, settings = make_client(tmp_path)
    manifest = settings.input_root / 'samples.csv'
    manifest.write_text('sample_id,read1,read2\nS1,a,b\n')
    with client:
        task = client.post('/api/tasks', json={'manifest_path': str(manifest)}).json()['id']
        response = client.get(f'/api/tasks/{task}/interpretation')
        assert response.status_code == 200
        assert response.json()['sections'] == []
        assert '未获得' in response.json()['text']
        assert client.get('/api/tasks/unknown/interpretation').status_code == 404


def test_successful_stage_missing_sample_tables_is_explicit(tmp_path):
    client, settings = make_client(tmp_path)
    manifest = settings.input_root / 'samples.csv'
    manifest.write_text('sample_id,read1,read2\nS1,a,b\nS2,c,d\n')
    with client:
        task = client.post('/api/tasks', json={'manifest_path': str(manifest)}).json()['id']
        repo = client.app.state.repository
        repo.update_step(task, 'taxonomy', status='succeeded')
        repo.update_step(task, 'functional_annotation', status='succeeded')
        body = client.get(f'/api/tasks/{task}/interpretation').json()
        assert 'S1' in ' '.join(body['warnings']) and 'S2' in ' '.join(body['warnings'])
        assert 'species_abundance.tsv' in ' '.join(body['warnings'])
        assert 'read_ko.tsv' in ' '.join(body['warnings'])


@pytest.mark.parametrize('case', ['nan', 'scope', 'duplicate', 'coverage_range', 'taxon_sum', 'foreign_qc'])
def test_unsupported_or_inconsistent_data_cannot_generate_claims(tmp_path, case):
    client, settings = make_client(tmp_path)
    manifest = settings.input_root / 'samples.csv'
    manifest.write_text('sample_id,read1,read2\nS1,a,b\n')
    with client:
        task = client.post('/api/tasks', json={'manifest_path': str(manifest)}).json()['id']
        repo = client.app.state.repository
        root = settings.state_root / 'outputs' / task
        if case == 'foreign_qc':
            repo.update_step(task, 'fastp', status='succeeded')
            file = root / 'fastp' / 'S9.fastp.json'
            file.parent.mkdir(parents=True)
            file.write_text(json.dumps({'summary': {'before_filtering': {'total_reads': 100}, 'after_filtering': {'total_reads': 90}}}))
        elif case == 'taxon_sum':
            repo.update_step(task, 'taxonomy', status='succeeded')
            file = root / 'taxonomy' / 'S1' / 'species_abundance.tsv'
            file.parent.mkdir(parents=True)
            file.write_text('sample_id\ttaxid\ttaxonomy\ttaxonomy_rank\tabundance\tabundance_unit\nS1\t1\tA\tspecies\t0.8\tfraction_of_reads\nS1\t2\tB\tspecies\t0.8\tfraction_of_reads\n')
        else:
            repo.update_step(task, 'functional_annotation', status='succeeded')
            coverage = case == 'coverage_range'
            file = root / 'functional_annotation' / 'S1' / ('read_pathcoverage.tsv' if coverage else 'read_ko.tsv')
            file.parent.mkdir(parents=True)
            header = 'sample_id\tfunction_source\tfunction_namespace\tfunction_id\tfunction_name\tfunction_type\tabundance\tabundance_unit\tanalysis_scope\n'
            value = 'nan' if case == 'nan' else '2'
            scope = 'taxon_stratified' if case == 'scope' else 'community_total'
            row = f'S1\tHUMAnN\t{"MetaCyc" if coverage else "KO"}\tK1\tExample\t{"pathway_coverage" if coverage else "ko"}\t{value}\tHUMAnN_reported\t{scope}\n'
            file.write_text(header + row + (row if case == 'duplicate' else ''))
        body = client.get(f'/api/tasks/{task}/interpretation').json()
        assert body['sections'] == []
        assert body['warnings']
