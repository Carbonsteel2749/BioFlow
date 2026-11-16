import csv
import hashlib
import json
import pytest
from test_typed_input_gateway import gateway, finish

SPECIES='sample_id taxid taxonomy taxonomy_rank taxonomy_system read_count_or_estimated_reads abundance abundance_unit classification_source database_profile database_release'.split()
FUNCTION='sample_id function_source function_namespace function_id function_name function_type abundance abundance_unit relative_abundance normalization_method evidence_level analysis_scope database_profile database_release'.split()

def species(**changes):
    return dict(zip(SPECIES,['s1','123','named','species','NCBI','2','0.25','fraction_of_reads','Kraken2+Bracken','p','r']))|changes

def functional(kind='gene_family',namespace='UniRef90',**changes):
    return dict(zip(FUNCTION,['s1','HUMAnN',namespace,'UniRef90_X','named',kind,'0','HUMAnN_reported','','HUMAnN raw output','reads','community_total','p','r']))|changes

def import_rows(gateway,rows,feature,unit,normalization):
    g,store,c,base,path,_=gateway
    with path.open('w',encoding='utf-8',newline='') as h:
        w=csv.DictWriter(h,fieldnames=list(rows[0]),delimiter='\t');w.writeheader();w.writerows(rows)
    with store.connect() as db:
        db.execute("UPDATE assets SET name='table.tsv',size_bytes=?,sha256=?",(path.stat().st_size,hashlib.sha256(path.read_bytes()).hexdigest()))
    response=c.post(base,json={'idempotency_key':'convert','spec':dict(kind='matrix',table='asset:a',feature_type=feature,unit=unit,normalization=normalization)})
    assert response.status_code==202,response.text
    jid=response.json()['id'];status=finish(c,base,jid)
    return status,c.get(base+'/'+jid),g,path

def test_species_pipeline_retains_original_and_archive(gateway):
    status,response,g,path=import_rows(gateway,[species(),species(taxid='456',abundance='0')],'species','fraction','none')
    assert status['status']=='ready',status
    record=response.json();conversion=record['origin']['conversion']
    assert conversion['included_rows']==2
    assert record['spec']['table']=='derived/canonical.tsv'
    from backend.app.services.input_datasets import InputDatasetService
    from backend.app.services.full_archive import typed_archive_entries
    from types import SimpleNamespace
    service=InputDatasetService(SimpleNamespace(input_root=g.input_root,state_root=g.input_root/'state'))
    jid=status['id'];assert service.get(jid)['origin']==record['origin']
    snapshot=service.snapshot({'source':jid},'run1')
    entries=typed_archive_entries(service.settings,'run1',snapshot)
    source=record['origin']['sources'][0];folder=service.root/jid
    assert (folder/source['staged_file']).read_bytes()==path.read_bytes()
    assert {folder/source['staged_file'],folder/'derived/canonical.tsv'} <= {p for p,n in entries}
    assert 'NCBI:123' in (folder/'derived/canonical.tsv').read_text()

@pytest.mark.parametrize('kind,namespace,feature,unit',[('gene_family','UniRef90','gene_families','HUMAnN_reported'),('ko','KO','KO','HUMAnN_reported'),('ec','EC','EC','HUMAnN_reported'),('pathway_abundance','MetaCyc','pathway_abundance','HUMAnN_reported'),('pathway_coverage','MetaCyc','pathway_coverage','coverage')])
def test_functional_pipeline_exclusions_and_zero(gateway,kind,namespace,feature,unit):
    rows=[functional(kind,namespace,function_id='named'),functional(kind,namespace,function_id='UNMAPPED'),functional(kind,namespace,function_id='named|taxon'),functional(kind,namespace,function_id='UNINTEGRATED|taxon')]
    status,response,g,path=import_rows(gateway,rows,feature,unit,'HUMAnN raw output')
    assert status['status']=='ready',status
    conversion=response.json()['origin']['conversion']
    assert [conversion[k] for k in ('input_rows','included_rows','excluded_diagnostics','excluded_strata')]==[4,1,2,1]
    if unit=='coverage':assert 'https://github.com/biobakery/humann' in conversion['rule']

@pytest.mark.parametrize('change',[{'abundance_unit':'percent'},{'normalization_method':'CPM'},{'function_namespace':'EC'},{'database_release':'different'},{'function_type':'ec'},{'analysis_scope':'unknown'}])
def test_mixed_context_fails_without_paths(gateway,change):
    status,response,g,path=import_rows(gateway,[functional(),functional(function_id='second',**change)],'gene_families','HUMAnN_reported','HUMAnN raw output')
    assert status['status']=='failed'
    assert str(path) not in status['error']

def test_no_named_features(gateway):
    status,_,_,_=import_rows(gateway,[functional(function_id='UniRef90_unknown')],'gene_families','HUMAnN_reported','HUMAnN raw output')
    assert status['status']=='failed' and 'community-total' in status['error']

def test_coverage_out_of_range(gateway):
    status,_,_,_=import_rows(gateway,[functional('pathway_coverage','MetaCyc',abundance='2')],'pathway_coverage','coverage','HUMAnN raw output')
    assert status['status']=='failed'

def test_conversion_free_space_growth(gateway,monkeypatch):
    from bioflow_platform import input_datasets as module
    original=module.available_space
    def check(root,required,reserve):
        if list(root.glob('typed-datasets/*/derived/canonical.tsv')):raise module.ImportDiagnostic('conversion reserve test')
        return original(root,required,reserve)
    monkeypatch.setattr(module,'available_space',check)
    status,_,_,path=import_rows(gateway,[species()],'species','fraction','none')
    assert status['status']=='failed' and status['error']=='conversion reserve test'
    assert path.is_file()

def test_canonical_pass_through(gateway):
    status,response,g,path=import_rows(gateway,[dict(sample_id='s1',feature_id='f',value='1234567890123456789012345678000')],'KO','counts','none')
    assert status['status']=='ready',status
    record=response.json();assert record['origin']['conversion'] is None
    assert (g.input_root/'typed-datasets'/status['id']/record['spec']['table']).read_bytes()==path.read_bytes()

@pytest.mark.parametrize('changes',[{'taxonomy_system':'unknown'},{'database_profile':'other'},{'taxonomy_rank':'genus'},{'abundance_unit':'percent'}])
def test_species_mixed_or_unknown_context(gateway,changes):
    status,_,_,_=import_rows(gateway,[species(),species(taxid='456',**changes)],'species','fraction','none')
    assert status['status']=='failed'

def test_duplicate_named_features_reach_authoritative_validator(gateway):
    status,_,_,_=import_rows(gateway,[functional(),functional()],'gene_families','HUMAnN_reported','HUMAnN raw output')
    assert status['status']=='failed'

def test_converter_caps_and_unknown_schema(tmp_path):
    from bioflow_platform.input_matrix_conversion import convert_matrix,ConversionError
    folder=tmp_path/'one';folder.mkdir()
    (folder/'m.tsv').write_text('x\ty\n1\t2\n')
    spec=dict(table='m.tsv',feature_type='species',unit='fraction',normalization='none')
    with pytest.raises(ConversionError,match='canonical'):convert_matrix(folder,spec,lambda n:None)
    with (folder/'m.tsv').open('w',newline='') as h:
        w=csv.DictWriter(h,fieldnames=SPECIES,delimiter='\t');w.writeheader();w.writerow(species())
    with pytest.raises(ConversionError,match='2000000'):convert_matrix(folder,spec,lambda n:None,max_rows=0)
