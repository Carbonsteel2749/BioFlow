from pathlib import Path

from backend.tests.test_uploads_and_preview import make_client


def task(client, settings, name):
    manifest = settings.input_root / (name + '.csv')
    manifest.write_text('sample_id,read1,read2\nS01,a,b\n')
    response = client.post('/api/tasks', json={'manifest_path': str(manifest)})
    assert response.status_code == 201
    return response.json()['id']


def artifact(client, settings, task_id, name='result.tsv', **kwargs):
    path = settings.state_root / 'outputs' / task_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('name\tabundance\nA\t1\n')
    return client.app.state.artifact_service.register(
        task_id=task_id, producer='test', path=path, media_type='text/tab-separated-values',
        artifact_type=kwargs.pop('artifact_type', 'taxonomy.species_abundance'),
        sample_scope='sample', sample_id='S01', **kwargs)


def test_results_filter_paginate_and_preserve_task_identity(tmp_path):
    client, settings = make_client(tmp_path)
    with client:
        a, b = task(client, settings, 'batch-a'), task(client, settings, 'batch-b')
        ar = artifact(client, settings, a)
        artifact(client, settings, b, artifact_type='functional.ko_abundance')
        client.app.state.repository.update_task(a, status='completed')
        response = client.get('/api/results?limit=1')
        assert response.status_code == 200
        assert response.json()['total'] == 2
        assert len(response.json()['items']) == 1
        rows = client.get('/api/results?sample_id=S01').json()['items']
        assert {r['task_id'] for r in rows} == {a, b}
        filtered = client.get('/api/results', params={'task_id': a, 'artifact_type': 'taxonomy.species_abundance'}).json()
        assert filtered['total'] == 1
        item = filtered['items'][0]
        assert item['artifact_id'] == ar['artifact_id']
        assert item['task_status'] == 'completed'
        assert item['partial'] is False
        assert str(settings.state_root) not in str(item)
        assert client.get('/api/results?limit=101').status_code == 422
        assert client.get('/api/results?offset=-1').status_code == 422
        assert client.get('/api/results?sample_id=absent').json()['total'] == 0


def test_results_missing_files_and_restricted_reads_cannot_download(tmp_path):
    client, settings = make_client(tmp_path)
    with client:
        tid = task(client, settings, 'batch')
        row = artifact(client, settings, tid)
        artifact(client, settings, tid, name='raw.fastq', artifact_type='reads.raw')
        (settings.state_root / 'outputs' / tid / 'result.tsv').unlink()
        result = client.get('/api/results')
        assert result.status_code == 200
        items = result.json()['items']
        # Raw reads are managed by data catalog, not offered as result downloads.
        assert len(items) == 1
        assert items[0]['status'] == 'missing'
        assert items[0]['downloadable'] is False
        assert items[0]['partial'] is True
        assert client.get(f"/api/artifacts/{row['artifact_id']}/download").status_code == 409


def test_results_changed_file_and_deleted_task_are_not_presented_as_available(tmp_path):
    client, settings = make_client(tmp_path)
    with client:
        a, b = task(client, settings, 'one'), task(client, settings, 'two')
        artifact(client, settings, a)
        artifact(client, settings, b)
        (settings.state_root / 'outputs' / a / 'result.tsv').write_text('changed')
        with client.app.state.repository._connect() as db:
            db.execute('DELETE FROM tasks WHERE id=?', (b,))
        response = client.get('/api/results')
        assert response.status_code == 200
        assert response.json()['total'] == 1
        assert response.json()['items'][0]['status'] == 'validation_failed'


def test_refresh_publishes_stage_figures_without_visiting_task_detail(tmp_path):
    from backend.tests.test_artifact_routes import _stage_plot
    client, settings = make_client(tmp_path)
    with client:
        tid = task(client, settings, 'stage-only')
        plots = _stage_plot(settings, tid)
        (plots / 'taxonomy.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        assert client.get('/api/results').json()['total'] == 0
        refresh = client.post('/api/results/refresh?limit=1')
        assert refresh.status_code == 200
        assert refresh.json()['processed'] == 1
        assert refresh.json()['remaining'] == 0
        assert client.get('/api/results').json()['total'] == 1
        client.post('/api/results/refresh?limit=1')
        assert client.get('/api/results').json()['total'] == 1


def test_aggregate_retains_recorded_parent_lineage(tmp_path):
    client, settings = make_client(tmp_path)
    with client:
        tid = task(client, settings, 'lineage')
        parent = artifact(client, settings, tid)
        child = artifact(client, settings, tid, name='derived.tsv', parent_artifact_ids=[parent['artifact_id']])
        response = client.get('/api/results').json()
        found = next(item for item in response['items'] if item['artifact_id'] == child['artifact_id'])
        assert found['derived_from'] == [parent['artifact_id']]


def test_shared_registry_workflow_result_and_dataset_association(tmp_path):
    from backend.tests.test_datasets import uploaded_dataset
    client, settings = make_client(tmp_path)
    with client:
        data = uploaded_dataset(client)
        client.app.state.workflow_run_repository.create_run(
            task_id='workflow-only', graph_hash='a'*64, compiled_root=tmp_path / 'compiled',
            nodes=[], topological_order=[], input_manifest=Path(data['manifest_path']),
        )
        artifact(client, settings, 'workflow-only')
        response = client.get('/api/results?task_id=workflow-only')
        assert response.status_code == 200
        item = response.json()['items'][0]
        assert item['task_kind'] == 'workflow'
        assert item['partial'] is True
        detail = client.get(f"/api/datasets/{data['dataset_id']}").json()
        assert detail['tasks'][0]['id'] == 'workflow-only'
