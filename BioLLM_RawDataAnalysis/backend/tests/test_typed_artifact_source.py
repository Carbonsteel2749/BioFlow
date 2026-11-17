import pytest
from backend.app.services.artifacts import ArtifactService

def test_source_file_is_contained_relative_and_never_guessed(tmp_path):
    service=ArtifactService(None,tmp_path)
    path=tmp_path/'task/nested/c.fa';path.parent.mkdir(parents=True);path.write_text('>c\nAC\n')
    assert service._source_file(dict(task_id='task',path=str(path)))=='nested/c.fa'
    assert service._source_file(dict(task_id='other',path=str(path))) is None
    assert service._source_file(dict(task_id='task',path=str(path.parent))) is None
    assert service._source_file(dict(task_id='../task',path=str(path))) is None

def test_source_file_rejects_symlink(tmp_path):
    service=ArtifactService(None,tmp_path);folder=tmp_path/'task';folder.mkdir()
    source=folder/'c.fa';source.write_text('>c\nAC\n');link=folder/'link.fa'
    try:link.symlink_to(source)
    except OSError:pytest.skip('symlink creation not permitted')
    assert service._source_file(dict(task_id='task',path=str(link))) is None

def test_source_descriptor_is_transport_only_not_manifest_input(tmp_path):
    import json,hashlib
    from pathlib import Path
    from backend.tests.test_typed_compatibility import environment
    from backend.app.artifacts import ArtifactRepository
    from backend.app.services.artifacts import ArtifactValidationError
    settings,repo,*_=environment(tmp_path)
    service=ArtifactService(ArtifactRepository(settings.database_path),settings.state_root/'outputs')
    folder=service.output_root/'typed1';folder.mkdir(parents=True)
    data=b'>c\nAC\n';(folder/'c.fa').write_bytes(data)
    entry=dict(producer='assembly',artifact_type='assembly.contigs',path='c.fa',media_type='text/plain',sha256=hashlib.sha256(data).hexdigest(),size_bytes=len(data))
    payload=dict(schema_version='1.0',task_id='typed1',artifacts=[entry]);manifest=folder/'manifest.json'
    manifest.write_text(json.dumps(payload))
    result=service.register_manifest(manifest)
    assert result[0]['source_file']=='c.fa'
    assert json.loads(manifest.read_text())==payload
    assert 'source_file' not in service.repository.get(result[0]['artifact_id'])
    schema=json.loads((Path(__file__).resolve().parents[2]/'workflow/assets/artifact.schema.json').read_text())
    assert schema['$defs']['artifact']['additionalProperties'] is False
    assert 'source_file' not in schema['$defs']['artifact']['properties']
    entry['source_file']='forged.fa';manifest.write_text(json.dumps(payload))
    with pytest.raises(ArtifactValidationError,match='unsupported artifact manifest fields'):service.register_manifest(manifest)
