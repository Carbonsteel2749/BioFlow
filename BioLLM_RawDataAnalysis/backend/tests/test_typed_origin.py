from pathlib import Path
from types import SimpleNamespace
import hashlib
import pytest
from backend.app.services.input_datasets import InputDatasetService
from backend.app.workflows.input_contracts import InputContractError
from backend.app.services.full_archive import typed_archive_entries


def setup(tmp_path):
    settings=SimpleNamespace(input_root=tmp_path/'input',state_root=tmp_path/'state')
    service=InputDatasetService(settings);folder=service.root/'one';folder.mkdir(parents=True)
    (folder/'c.fa').write_text('>c\nACGT\n');evidence=folder/'original.tsv';evidence.write_bytes(b'complete original evidence')
    origin={'authority':'owned_import_gateway','sources':[{'id':'asset:a','name':'original.tsv','size_bytes':evidence.stat().st_size,'sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),'staged_file':'original.tsv','source_task':None,'node':None,'producer':None}], 'conversion':None}
    return service,settings,origin


def test_origin_frozen_verified_and_archived(tmp_path):
    service,settings,origin=setup(tmp_path)
    record=service.register('one',{'kind':'contigs','cohort_id':'c','fasta':'c.fa'},origin=origin)
    assert record['origin']==origin
    path=service.snapshot_metadata_only({'source':'one'},'run1')
    assert service.verify_snapshot(path)['bindings']['source']['origin']==origin
    assert any(p.name=='original.tsv' for p,name in typed_archive_entries(settings,'run1',path))
    (service.root/'one/original.tsv').write_bytes(b'changed original evidence')
    with pytest.raises(InputContractError,match='SHA'):service.verify_snapshot(path)


@pytest.mark.parametrize('change',['escape','trust','oversize','hash'])
def test_origin_rejects_invalid_evidence(tmp_path,change):
    service,settings,origin=setup(tmp_path)
    if change=='escape':origin['sources'][0]['staged_file']='../original.tsv'
    if change=='trust':origin['trust']='platform_verified'
    if change=='oversize':origin['sources'][0]['name']='x'*5000000
    if change=='hash':origin['sources'][0]['sha256']='0'*64
    with pytest.raises(InputContractError):service.register('one',{'kind':'contigs','cohort_id':'c','fasta':'c.fa'},origin=origin)
