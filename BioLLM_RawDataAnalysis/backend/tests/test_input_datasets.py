import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.app.services.input_datasets import InputDatasetService
from backend.app.workflows.input_contracts import InputContractError


def setup(tmp_path):
    settings=SimpleNamespace(input_root=tmp_path/'incoming',state_root=tmp_path/'state')
    service=InputDatasetService(settings);folder=settings.input_root/'typed-datasets'/'abc123'
    folder.mkdir(parents=True);(folder/'contigs.fa').write_text('>c1\nACGTN\n')
    spec={'kind':'contigs','cohort_id':'joint','fasta':'contigs.fa'}
    return service,folder,spec


def test_registration_is_immutable_and_rechecks_file_content(tmp_path):
    service,folder,spec=setup(tmp_path)
    result=service.register('abc123',spec)
    assert result['dataset_id']=='abc123' and result['contract']['data_type']=='assembly_fasta'
    assert service.register('abc123',spec)==result
    with pytest.raises(InputContractError,match='不可变'):service.register('abc123',{**spec,'cohort_id':'other'})
    (folder/'contigs.fa').write_text('>c1\nTTTTN\n')
    with pytest.raises(InputContractError,match='校验'):service.get('abc123')


def test_registration_does_not_accept_arbitrary_paths_or_tampered_contract(tmp_path):
    service,folder,spec=setup(tmp_path)
    with pytest.raises(InputContractError):service.register('../abc123',spec)
    service.register('abc123',spec)
    path=folder/'contract.json';data=json.loads(path.read_text());data['contract']['trust']='platform_verified';path.write_text(json.dumps(data))
    with pytest.raises(InputContractError,match='校验'):service.get('abc123')


def test_task_snapshot_pins_contract_and_inputs_without_modifying_registered_dataset(tmp_path):
    service,folder,spec=setup(tmp_path);result=service.register('abc123',spec)
    snapshot=service.snapshot({'contigs':'abc123'},'task1')
    body=json.loads(snapshot.read_text())
    assert body['bindings']['contigs']['contract']['cohort_id']=='joint'
    assert body['bindings']['contigs']['manifest_sha256']==result['contract']['manifest_sha256']
    assert service.verify_snapshot(snapshot)==body
    (folder/'contigs.fa').write_text('>c1\nAAAA\n')
    with pytest.raises(InputContractError,match='校验'):service.verify_snapshot(snapshot)


def test_snapshot_validates_node_ids_and_never_replaces_existing_history(tmp_path):
    service,folder,spec=setup(tmp_path);service.register('abc123',spec)
    with pytest.raises(InputContractError):service.snapshot({'../../x':'abc123'},'task1')
    first=service.snapshot({'x':'abc123'},'task1')
    with pytest.raises(InputContractError,match='已存在'):service.snapshot({'y':'abc123'},'task1')
    assert 'x' in json.loads(first.read_text())['bindings']


def test_oversize_contract_is_not_published_and_oversize_snapshot_not_written(tmp_path,monkeypatch):
    from backend.app.services import input_datasets as module
    service,folder,spec=setup(tmp_path)
    monkeypatch.setattr(module,'MAX_CONTRACT_BYTES',20,raising=False)
    with pytest.raises(InputContractError,match='上限'):service.register('abc123',spec)
    assert not (folder/'contract.json').exists()
    monkeypatch.setattr(module,'MAX_CONTRACT_BYTES',4*1024*1024)
    service.register('abc123',spec)
    monkeypatch.setattr(module,'MAX_SNAPSHOT_BYTES',20,raising=False)
    with pytest.raises(InputContractError,match='上限'):service.snapshot({'x':'abc123'},'task1')
    assert not (service.settings.state_root/'workflow_runs/task1/inputs/bindings.json').exists()
