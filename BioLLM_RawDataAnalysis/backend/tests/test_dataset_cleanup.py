import csv
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
import pytest

from backend.tests.test_datasets import uploaded_dataset
from backend.tests.test_uploads_and_preview import make_client


def setup_case(tmp_path):
    client, settings = make_client(tmp_path)
    data = uploaded_dataset(client)
    task = client.post('/api/tasks', json={'manifest_path': data['manifest_path']}).json()
    client.app.state.repository.update_task(task['id'], status='cancelled')
    return client, settings, data, task['id']


def preview(client, task, sync=True):
    response = client.get(f'/api/tasks/{task}/cleanup-preview', params={'mode': 'delete', 'delete_dataset': sync})
    assert response.status_code == 200, response.text
    return response.json()


def remove(client, task, plan, sync=True):
    return client.post(f'/api/tasks/{task}/cleanup', json={'mode': 'delete', 'delete_dataset': sync,
                       'confirmation': task, 'token': plan['token']})


def test_opted_in_cleanup_removes_exclusive_uploads_manifest_and_catalog(tmp_path):
    client, settings, data, task = setup_case(tmp_path)
    plan = preview(client, task)
    assert plan['dataset_cleanup']['will_delete'] is True
    assert remove(client, task, plan).status_code == 200
    assert client.get(f"/api/datasets/{data['dataset_id']}").status_code == 404
    assert not Path(data['manifest_path']).exists()
    assert not list((settings.input_root / 'uploads').glob('*/reads*'))
    assert not list((settings.input_root / 'manifests').glob('*.reads/*'))
    client.app.state.dataset_service.initialize()
    assert client.get('/api/datasets').json()['total'] == 0


def test_default_cleanup_keeps_dataset(tmp_path):
    client, _, data, task = setup_case(tmp_path)
    assert remove(client, task, preview(client, task, False), False).status_code == 200
    assert client.get(f"/api/datasets/{data['dataset_id']}").json()['status'] == 'available'


def test_shared_task_preserves_data_and_explains_why(tmp_path):
    client, _, data, task = setup_case(tmp_path)
    other = client.post('/api/tasks', json={'manifest_path': data['manifest_path']}).json()['id']
    plan = preview(client, task)
    assert plan['dataset_cleanup']['will_delete'] is False
    assert other in str(plan['dataset_cleanup']['reasons'])
    assert remove(client, task, plan).status_code == 200
    assert Path(data['manifest_path']).exists()
    assert client.get(f'/api/tasks/{other}').status_code == 200


def test_shared_physical_reads_across_manifests_are_preserved(tmp_path):
    client, _, data, task = setup_case(tmp_path)
    sample = client.get(f"/api/datasets/{data['dataset_id']}").json()['samples'][0]
    other = client.post('/api/uploads/manifests', json={'files': [{'sample_id': 'Other',
        'read1_upload_id': sample['read1']['upload_id'], 'read2_upload_id': sample['read2']['upload_id']}]}).json()
    plan = preview(client, task)
    assert plan['dataset_cleanup']['will_delete'] is False
    assert remove(client, task, plan).status_code == 200
    assert client.get(f"/api/datasets/{other['dataset_id']}").json()['status'] == 'available'


def test_new_reference_invalidates_delete_confirmation(tmp_path):
    client, _, data, task = setup_case(tmp_path)
    plan = preview(client, task)
    client.post('/api/tasks', json={'manifest_path': data['manifest_path']})
    assert remove(client, task, plan).status_code == 409
    assert Path(data['manifest_path']).exists()
    assert client.get(f'/api/tasks/{task}').status_code == 200


def test_untracked_hardlink_preserves_data(tmp_path):
    client, _, data, task = setup_case(tmp_path)
    with open(data['manifest_path']) as handle:
        read = next(csv.DictReader(handle))['read1']
    os.link(read, tmp_path / 'manual.fastq.gz')
    plan = preview(client, task)
    assert plan['dataset_cleanup']['will_delete'] is False
    assert remove(client, task, plan).status_code == 200
    assert Path(read).exists()


def test_dataset_delete_blocks_referenced_data_then_deletes_orphan(tmp_path):
    client, _, data, task = setup_case(tmp_path)
    url = f"/api/datasets/{data['dataset_id']}/cleanup"
    plan = client.get(url + '-preview').json()
    assert plan['dataset_cleanup']['will_delete'] is False
    assert client.post(url, json={'confirmation': data['dataset_id'], 'token': plan['token']}).status_code == 409
    assert remove(client, task, preview(client, task, False), False).status_code == 200
    plan = client.get(url + '-preview').json()
    assert client.post(url, json={'confirmation': data['dataset_id'], 'token': plan['token']}).status_code == 200
    assert not Path(data['manifest_path']).exists()


def test_sync_option_is_bound_to_confirmation(tmp_path):
    client, _, data, task = setup_case(tmp_path)
    assert remove(client, task, preview(client, task, False), True).status_code == 409
    assert Path(data['manifest_path']).exists()


def test_manually_managed_history_is_not_deleted(tmp_path):
    client, settings, data, task = setup_case(tmp_path)
    manifest = settings.input_root / 'manual.csv'
    manifest.write_bytes(Path(data['manifest_path']).read_bytes())
    client.app.state.repository.update_task(task, status='cancelled')
    # A known but uncatalogued manifest is still a reference.
    client.app.state.repository.create_task('external-task', str(manifest), ['validate'])
    assert preview(client, task)['dataset_cleanup']['will_delete'] is False


def test_mounted_input_directory_is_never_deleted(tmp_path, monkeypatch):
    client, _, data, task = setup_case(tmp_path)
    mounted = Path(data['manifest_path']).with_suffix('.reads')
    original = os.path.ismount
    monkeypatch.setattr(os.path, 'ismount', lambda p: Path(p) == mounted or original(p))
    assert preview(client, task)['dataset_cleanup']['will_delete'] is False


def test_file_open_by_other_process_preserves_raw_reads(tmp_path):
    client, _, data, task = setup_case(tmp_path)
    with open(data['manifest_path']) as handle:
        read = next(csv.DictReader(handle))['read1']
    process = subprocess.Popen([sys.executable, '-c', 'import sys,time; f=open(sys.stdin.readline().strip(),"rb"); print("ready",flush=True); time.sleep(30)'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        process.stdin.write(read+'\n')
        process.stdin.flush()
        assert process.stdout.readline().strip() == 'ready'
        assert preview(client, task)['dataset_cleanup']['will_delete'] is False
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_reuse_returns_not_found_when_catalog_row_disappears(tmp_path, monkeypatch):
    client, _, data, _ = setup_case(tmp_path)
    service = client.app.state.dataset_service
    original = service.get
    def removed_after_read(dataset_id):
        result = original(dataset_id)
        with client.app.state.repository._connect() as db:
            db.execute('DELETE FROM datasets WHERE id=?', (dataset_id,))
        return result
    monkeypatch.setattr(service, 'get', removed_after_read)
    response = client.post(f"/api/datasets/{data['dataset_id']}/reuse")
    assert response.status_code == 404


def test_reuse_and_delete_are_serialized(tmp_path, monkeypatch):
    client, _, data, task = setup_case(tmp_path)
    assert remove(client, task, preview(client, task, False), False).status_code == 200
    url = f"/api/datasets/{data['dataset_id']}/cleanup"
    plan = client.get(url+'-preview').json()
    entered, release, deleting = threading.Event(), threading.Event(), threading.Event()
    service = client.app.state.dataset_service
    original = service.get
    def held_get(dataset_id):
        result = original(dataset_id)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(service, 'get', held_get)
    def delete():
        deleting.set()
        return client.post(url, json={'token': plan['token'], 'confirmation': data['dataset_id']})
    with ThreadPoolExecutor(max_workers=2) as pool:
        reuse = pool.submit(client.post, f"/api/datasets/{data['dataset_id']}/reuse")
        assert entered.wait(5)
        deletion = pool.submit(delete)
        try:
            assert deleting.wait(5)
            with pytest.raises(TimeoutError):
                deletion.result(timeout=0.2)
        finally:
            release.set()
        assert reuse.result(timeout=5).status_code == 200
        assert deletion.result(timeout=5).status_code == 200


@pytest.mark.parametrize('reference', ['workflow_manifest', 'registered_artifact'])
def test_node_workflow_and_cross_module_artifact_references_keep_data(tmp_path, reference):
    client, settings, data, task = setup_case(tmp_path)
    client.app.state.workflow_run_repository.create_run(task_id='node-consumer', graph_hash='a'*64,
        compiled_root=settings.state_root/'workflow', nodes=[], topological_order=[],
        input_manifest=Path(data['manifest_path']) if reference == 'workflow_manifest' else None)
    if reference == 'registered_artifact':
        with open(data['manifest_path']) as handle:
            path = next(csv.DictReader(handle))['read1']
        with client.app.state.repository._connect() as db:
            db.execute('''INSERT INTO workflow_artifacts(task_id,port_id,path,sha256,size_bytes,created_at)
                VALUES(?,?,?,?,?,?)''', ('node-consumer', 'reads', path, 'a'*64, Path(path).stat().st_size, '2026-09-23'))
    plan = preview(client, task)
    assert plan['dataset_cleanup']['will_delete'] is False
    assert remove(client, task, plan).status_code == 200
    assert Path(data['manifest_path']).exists()
