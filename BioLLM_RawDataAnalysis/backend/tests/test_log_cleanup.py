from backend.tests.test_task_cleanup import case
import pytest


def test_only_selected_logs_removed_with_confirmed_fresh_preview(case):
    client, task, settings, paths, original = case
    repo = client.app.state.repository
    repo.update_step(task, 'validate', status='succeeded')
    log = paths['logs'] / 'global.validate.log'
    log.write_text('safe progress log')
    audit = paths['logs'] / 'diagnostic_audit.jsonl'
    audit.write_text('audit')
    url = f'/api/tasks/{task}/log-files'
    response = client.get(url)
    assert response.status_code == 200
    entry = next(f for f in response.json()['files'] if f['name'] == 'global.validate.log')
    preview = client.post(url+'/cleanup-preview', json={'ids': [entry['id']]}).json()
    assert preview['estimated_bytes'] == len('safe progress log')
    deleted = client.post(url+'/cleanup', json={'ids': [entry['id']], 'token': preview['token'], 'confirmation': task})
    assert deleted.status_code == 200
    assert not log.exists() and audit.exists() and original.exists() and paths['work'].exists()
    assert '已由用户清理' in client.get(url+'/'+entry['id']).json()['text']
    assert client.get(f'/api/tasks/{task}').json()['status'] == 'cancelled'


def test_log_cleanup_rejects_running_tampered_token_and_protected_files(case):
    client, task, settings, paths, original = case
    repo = client.app.state.repository
    repo.update_step(task, 'validate', status='succeeded')
    log = paths['logs'] / 'global.validate.log'; log.write_text('one')
    url = f'/api/tasks/{task}/log-files'
    entry = client.get(url).json()['files'][0]
    preview = client.post(url+'/cleanup-preview', json={'ids': [entry['id']]}).json()
    log.write_text('changed')
    data={'ids':[entry['id']], 'token':preview['token'], 'confirmation':task}
    assert client.post(url+'/cleanup', json=data).status_code == 409
    repo.update_task(task, status='running')
    assert client.post(url+'/cleanup-preview', json={'ids':[entry['id']]}).status_code == 409
    repo.update_task(task, status='paused', error_message='original error')
    repo.update_step(task, 'validate', status='failed')
    assert client.post(url+'/cleanup-preview', json={'ids':[entry['id']]}).status_code == 409
    assert client.post(url+'/cleanup-preview', json={'ids':['../../secret']}).status_code == 409
    assert log.exists()


@pytest.mark.parametrize('kind', ['symlink','hardlink','referenced','confirmation'])
def test_log_cleanup_preserves_unsafe_or_unconfirmed_files(case, kind):
    client, task, settings, paths, original = case
    client.app.state.repository.update_step(task, 'validate', status='succeeded')
    log = paths['logs'] / 'global.validate.log'
    if kind == 'symlink':
        log.symlink_to(original)
    elif kind == 'hardlink':
        import os
        os.link(original, log)
    else:
        log.write_text('progress')
    if kind == 'referenced':
        # Any registered artifact must remain available after log cleanup.
        client.app.state.artifact_service.register(task_id=task,producer='test',artifact_type='provenance.run',path=paths['outputs']/'data.txt',media_type='text/plain')
        with client.app.state.artifact_repository._connect() as connection:
            connection.execute('UPDATE artifacts SET path=? WHERE task_id=?', (str(log),task))
    url=f'/api/tasks/{task}/log-files'
    entries=client.get(url).json()['files']
    if kind == 'symlink':
        assert not entries
    else:
        preview=client.post(url+'/cleanup-preview',json={'ids':[entries[0]['id']]})
        if kind == 'confirmation':
            assert preview.status_code == 200
            assert client.post(url+'/cleanup',json={'ids':[entries[0]['id']],'token':preview.json()['token'],'confirmation':'wrong'}).status_code == 409
        else:
            assert preview.status_code == 409
    assert log.exists() and original.exists()


def test_main_log_protected_after_successful_retry_clears_error(case):
    client, task, settings, paths, original = case
    repo = client.app.state.repository
    repo.update_task(task, status='completed', error_message=None)
    repo.update_step(task, 'validate', status='succeeded')
    log = paths['logs'] / 'validate.log'
    log.write_text('prior attempt failed\nretry succeeded\n')
    url = f'/api/tasks/{task}/log-files'
    entry = next(e for e in client.get(url).json()['files'] if e['name'] == 'validate.log')
    assert entry['selectable'] is False
    assert client.post(url+'/cleanup-preview', json={'ids':[entry['id']]}).status_code == 409
    assert log.exists()
