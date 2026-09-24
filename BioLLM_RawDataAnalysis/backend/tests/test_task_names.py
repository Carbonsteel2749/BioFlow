from pathlib import Path

import pytest

from backend.tests.test_dataset_cleanup import setup_case


def test_rename_is_persistent_and_propagates_without_changing_identity(tmp_path):
    client, _, data, task = setup_case(tmp_path)
    before = client.get(f'/api/tasks/{task}').json()
    response = client.patch(f'/api/tasks/{task}', json={'name': '  肠道菌群试运行  '})
    assert response.status_code == 200
    assert response.json()['name'] == '肠道菌群试运行'
    after = client.get(f'/api/tasks/{task}').json()
    for key in ('id', 'manifest_path', 'parameters', 'status', 'created_at'):
        assert after[key] == before[key]
    assert client.get('/api/tasks').json()[0]['name'] == '肠道菌群试运行'
    assert client.get(f"/api/datasets/{data['dataset_id']}").json()['tasks'][0]['name'] == '肠道菌群试运行'
    out = client.app.state.settings.state_root / 'outputs' / task / 'table.tsv'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('x\t1\n')
    client.app.state.artifact_service.register(task_id=task, producer='report', artifact_type='report.summary', path=out, media_type='text/plain')
    assert client.get('/api/results').json()['items'][0]['task_name'] == '肠道菌群试运行'


@pytest.mark.parametrize('name', ['', '   ', 'x' * 121, 'line\nbreak', '\x00bad'])
def test_invalid_names_are_rejected(tmp_path, name):
    client, _, _, task = setup_case(tmp_path)
    assert client.patch(f'/api/tasks/{task}', json={'name': name}).status_code == 422


def test_task_creation_accepts_name_and_unknown_task_cannot_be_renamed(tmp_path):
    client, _, data, _ = setup_case(tmp_path)
    response = client.post('/api/tasks', json={'manifest_path': data['manifest_path'], 'name': '命名分析'})
    assert response.status_code == 201
    assert response.json()['name'] == '命名分析'
    assert client.patch('/api/tasks/missing', json={'name': '名称'}).status_code == 404
