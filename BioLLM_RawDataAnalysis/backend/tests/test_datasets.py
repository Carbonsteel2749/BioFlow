import csv
from pathlib import Path

from backend.app.main import create_app
from fastapi.testclient import TestClient
from backend.tests.test_uploads_and_preview import make_client, upload


def uploaded_dataset(client, sample='S01'):
    r1 = upload(client, f'{sample}_R1.fastq.gz', f'{sample}/1')
    r2 = upload(client, f'{sample}_R2.fastq.gz', f'{sample}/2')
    response = client.post('/api/uploads/manifests', json={'files': [{
        'sample_id': sample, 'read1_upload_id': r1['id'], 'read2_upload_id': r2['id'],
    }]})
    assert response.status_code == 201
    return response.json()


def test_upload_registers_dataset_and_reuse_does_not_create_task_or_copy(tmp_path):
    client, settings = make_client(tmp_path)
    with client:
        manifest = uploaded_dataset(client)
        listing = client.get('/api/datasets')
        assert listing.status_code == 200
        assert listing.json()['total'] == 1
        dataset = listing.json()['items'][0]
        before = sorted(str(p) for p in settings.input_root.rglob('*'))
        response = client.post(f"/api/datasets/{dataset['id']}/reuse")
        assert response.status_code == 200
        assert response.json()['manifest_path'] == manifest['manifest_path']
        assert client.get('/api/tasks').json() == []
        assert sorted(str(p) for p in settings.input_root.rglob('*')) == before
        detail = client.get(f"/api/datasets/{dataset['id']}").json()
        assert detail['status'] == 'available'
        assert detail['samples'][0]['sample_id'] == 'S01'
        assert detail['samples'][0]['read1']['validation'] == 'upload_initial_check'
        assert detail['samples'][0]['read1']['name'] == 'S01_R1.fastq.gz'
        assert 'stored_path' not in str(detail)
        assert str(settings.input_root) not in str(detail)


def test_same_sample_names_remain_in_separate_datasets_and_tasks_link(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        first, second = uploaded_dataset(client), uploaded_dataset(client)
        assert first['dataset_id'] != second['dataset_id']
        for _ in range(2):
            response = client.post('/api/tasks', json={
                'dataset_id': first['dataset_id'], 'manifest_path': first['manifest_path'],
            })
            assert response.status_code == 201
        detail = client.get(f"/api/datasets/{first['dataset_id']}").json()
        assert len(detail['tasks']) == 2
        assert client.get(f"/api/datasets/{second['dataset_id']}").json()['tasks'] == []
        assert client.get('/api/datasets?limit=1&offset=1').json()['total'] == 2
        assert len(client.get('/api/datasets?limit=1&offset=1').json()['items']) == 1
        assert client.get('/api/datasets?limit=0').status_code == 422


def test_reuse_and_submit_recheck_missing_changed_and_mismatched_files(tmp_path):
    client, _ = make_client(tmp_path)
    with client:
        first, second = uploaded_dataset(client), uploaded_dataset(client)
        mismatch = client.post('/api/tasks', json={
            'dataset_id': first['dataset_id'], 'manifest_path': second['manifest_path'],
        })
        assert mismatch.status_code == 422
        with open(first['manifest_path']) as handle:
            read1 = Path(next(csv.DictReader(handle))['read1'])
        read1.unlink()
        assert client.post(f"/api/datasets/{first['dataset_id']}/reuse").status_code == 409
        assert client.post('/api/tasks', json={
            'dataset_id': first['dataset_id'], 'manifest_path': first['manifest_path'],
        }).status_code == 422
        assert client.get('/api/tasks').json() == []
        Path(second['manifest_path']).write_text('sample_id,read1,read2\nCHANGED,a,b\n')
        assert client.post(f"/api/datasets/{second['dataset_id']}/reuse").status_code == 409


def test_backfill_is_idempotent_and_does_not_guess_unpaired_uploads(tmp_path):
    client, settings = make_client(tmp_path)
    with client:
        data = uploaded_dataset(client)
        upload(client, 'lonely_R1.fastq.gz', 'lonely/1')
        # Simulate a pre-catalog installation while retaining the original files.
        with client.app.state.repository._connect() as connection:
            connection.execute('DELETE FROM dataset_samples')
            connection.execute('DELETE FROM datasets')
    for _ in range(2):
        with TestClient(create_app(settings)) as restarted:
            listing = restarted.get('/api/datasets').json()
            assert listing['total'] == 1
            assert listing['items'][0]['id'] == data['dataset_id']
            pending = restarted.get('/api/datasets-unpaired-uploads').json()
            assert pending['total'] == 1
            assert pending['items'][0]['original_name'] == 'lonely_R1.fastq.gz'


def test_historical_invalid_manifest_remains_visible_but_cannot_be_reused(tmp_path):
    client, settings = make_client(tmp_path)
    path = settings.input_root / 'legacy.csv'
    path.write_text('sample_id,read1,read2\nS01,a,b\nS01,c,d\n')
    with client:
        client.post('/api/tasks', json={'manifest_path': str(path)})
        listing = client.get('/api/datasets').json()
        assert listing['total'] == 1
        dataset = listing['items'][0]
        assert dataset['status'] == 'needs_review'
        assert client.post(f"/api/datasets/{dataset['id']}/reuse").status_code == 409


def test_external_and_symlink_reads_are_not_exposed_or_reused(tmp_path):
    client, settings = make_client(tmp_path)
    outside = tmp_path / 'private.fastq'
    outside.write_text('@read\nAC\n+\nII\n')
    link = settings.input_root / 'S01_R1.fastq'
    link.symlink_to(outside)
    manifest = settings.input_root / 'unsafe.csv'
    manifest.write_text(f'sample_id,read1,read2\nS01,{link},{outside}\n')
    with client:
        client.post('/api/tasks', json={'manifest_path': str(manifest)})
        dataset = client.get('/api/datasets').json()['items'][0]
        detail = client.get(f"/api/datasets/{dataset['id']}")
        assert str(outside) not in detail.text
        assert dataset['status'] != 'available'
        assert client.post(f"/api/datasets/{dataset['id']}/reuse").status_code == 409


def test_dataset_shows_registered_processed_reads_without_offering_reuse(tmp_path):
    client, settings = make_client(tmp_path)
    with client:
        data = uploaded_dataset(client)
        task = client.post('/api/tasks', json={'manifest_path': data['manifest_path']}).json()
        output = settings.state_root / 'outputs' / task['id'] / 'clean.fastq'
        output.parent.mkdir(parents=True)
        output.write_text('@S01\nAC\n+\nII\n')
        record = client.app.state.artifact_service.register(
            task_id=task['id'], producer='fastp', artifact_type='reads.clean', path=output,
            media_type='application/octet-stream', sample_scope='sample', sample_id='S01',
        )
        detail = client.get(f"/api/datasets/{data['dataset_id']}").json()
        assert detail['processed_reads'][0]['artifact_id'] == record['artifact_id']
        assert detail['processed_reads'][0]['task_id'] == task['id']
        assert 'path' not in detail['processed_reads'][0]


def test_external_task_added_after_start_is_discovered_once(tmp_path):
    client, settings = make_client(tmp_path)
    path = settings.input_root / 'external.csv'
    path.write_text('sample_id,read1,read2\nS01,a,b\n')
    with client:
        client.app.state.repository.create_task('external', str(path), ['validate'])
        assert client.get('/api/datasets').json()['total'] == 1
        assert client.get('/api/datasets').json()['total'] == 1


def test_dataset_detail_samples_and_tasks_are_paged_and_searchable(tmp_path):
    client, settings = make_client(tmp_path)
    with client:
        data = uploaded_dataset(client)
        with client.app.state.repository._connect() as db:
            original = db.execute('SELECT * FROM dataset_samples').fetchone()
            for i in range(25):
                db.execute('INSERT INTO dataset_samples VALUES(?,?,?,?)', (
                    data['dataset_id'], f'B{i:02}', original['read1_json'], original['read2_json']))
        for i in range(25):
            client.app.state.repository.create_task(f'task{i}', data['manifest_path'], ['validate'])
        url = f"/api/datasets/{data['dataset_id']}"
        first = client.get(url).json()
        assert first['sample_count'] == 26
        assert len(first['samples']) == 20
        assert len(first['tasks']) == 20
        last = client.get(url+'?sample_offset=20&task_offset=20').json()
        assert len(last['samples']) == 6
        assert len(last['tasks']) == 5
        filtered = client.get(url+'?sample_q=B02').json()
        assert filtered['sample_total'] == 1
        assert filtered['samples'][0]['sample_id'] == 'B02'
