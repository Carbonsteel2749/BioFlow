import pytest
from backend.app.workflows.input_contracts import validate_input_contract, InputContractError

@pytest.mark.parametrize('value',['1e101','1e-101','1.1234567890123456789012345678'])
def test_numeric_policy_rejects_without_rounding(tmp_path,value):
    (tmp_path/'m.tsv').write_text('sample_id\tfeature_id\tvalue\ns\tf\t'+value+'\n')
    with pytest.raises(InputContractError,match='28|100'):
        validate_input_contract(dict(kind='matrix',table='m.tsv',feature_type='KO',unit='RPK',normalization='none'),tmp_path)

@pytest.mark.parametrize('value',['1e100','1e-100','1234567890123456789012345678000','0e999'])
def test_numeric_policy_boundaries(tmp_path,value):
    (tmp_path/'m.tsv').write_text('sample_id\tfeature_id\tvalue\ns\tf\t'+value+'\n')
    contract=validate_input_contract(dict(kind='matrix',table='m.tsv',feature_type='KO',unit='RPK',normalization='none'),tmp_path)
    assert contract['numeric_policy']['significant_digits']==28

def test_summary_disparate_sums_and_plot_skip(tmp_path,monkeypatch):
    import csv,json
    from decimal import Decimal,localcontext
    from backend.tests.test_typed_execution import adapter
    source=tmp_path/'source';source.mkdir()
    text='sample_id\tfeature_id\tvalue\ns\tf1\t1e100\ns\tf2\t1e-100\n'
    (source/'m.tsv').write_text(text)
    contract=validate_input_contract(dict(kind='matrix',table='m.tsv',feature_type='KO',unit='RPK',normalization='none'),source)
    (source/'input.provenance.json').write_text(json.dumps(contract))
    module=adapter(monkeypatch)
    assert module.plot_coordinate(Decimal('1e999')) is None
    assert module.plot_coordinate(Decimal('1e-999')) is None
    monkeypatch.setattr(module,'plot_coordinate',lambda value:None)
    module.matrix_summary(source,tmp_path/'result')
    with (tmp_path/'result/sample_summary.tsv').open() as h:rows=list(csv.DictReader(h,delimiter='\t'))
    with localcontext() as context:
        context.prec=237
        assert Decimal(rows[0]['sum'])==Decimal('1e100')+Decimal('1e-100')
    assert '1e-100' in (tmp_path/'result/canonical.tsv').read_text()
    provenance=json.loads((tmp_path/'result/summary.provenance.json').read_text())
    assert provenance['plots']['feature_means']['status']=='skipped'
    assert provenance['numeric_policy']['mean_precision']==28

def test_coverage_summary_is_non_additive(tmp_path,monkeypatch):
    import csv,json
    from backend.tests.test_typed_execution import adapter
    source=tmp_path/'source';source.mkdir()
    (source/'m.tsv').write_text('sample_id\tfeature_id\tvalue\ns\tp1\t0.2\ns\tp2\t0.8\n')
    contract=validate_input_contract(dict(kind='matrix',table='m.tsv',feature_type='pathway_coverage',unit='coverage',normalization='HUMAnN raw output'),source)
    (source/'input.provenance.json').write_text(json.dumps(contract))
    adapter(monkeypatch).matrix_summary(source,tmp_path/'result')
    with (tmp_path/'result/sample_summary.tsv').open() as h:rows=list(csv.DictReader(h,delimiter='\t'))
    assert rows[0]['sum']=='' and rows[0]['mean_observed']=='0.5'
    provenance=json.loads((tmp_path/'result/summary.provenance.json').read_text())
    assert provenance['sum_policy']=='non_additive_coverage_confidence'
    assert 'non_additive_coverage_confidence' in (tmp_path/'result/feature_means.caption.md').read_text()
