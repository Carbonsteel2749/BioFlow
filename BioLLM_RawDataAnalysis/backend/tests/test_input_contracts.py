import gzip
from pathlib import Path
import pytest
from backend.app.workflows.input_contracts import InputContractError, validate_input_contract


def put(root,name,text):
    p=root/name;p.write_text(text,encoding='utf-8');return name


def reads(root,stage='raw'):
    a=put(root,'s_R1.fastq','@p1/1\nACGT\n+\nIIII\n@p2/1\nTGCA\n+\nIIII\n')
    b=put(root,'s_R2.fastq','@p1/2\nACGT\n+\nIIII\n@p2/2\nTGCA\n+\nIIII\n')
    return {'kind':'reads','processing_status':stage,'samples':[{'sample_id':'s','read1':a,'read2':b}]}


def test_raw_reads_validated_fully_with_content_sha(tmp_path):
    result=validate_input_contract(reads(tmp_path),tmp_path)
    assert result['data_type']=='paired_raw_reads'
    assert result['trust']=='external_unverified'
    assert result['samples'][0]['pairs']==2
    assert len(result['files'][0]['sha256'])==64


@pytest.mark.parametrize('stage,dtype',[('clean','paired_clean_reads'),('host_removed','paired_host_removed_reads')])
def test_processed_reads_require_note_and_confirmation_and_remain_unverified(tmp_path,stage,dtype):
    spec=reads(tmp_path,stage)
    with pytest.raises(InputContractError,match='说明'):validate_input_contract(spec,tmp_path)
    spec['processing_note']='外部流程完成质控并保留配对，工具和参数见实验记录。'
    with pytest.raises(InputContractError,match='确认'):validate_input_contract(spec,tmp_path)
    spec['confirmed']=True
    result=validate_input_contract(spec,tmp_path)
    assert result['data_type']==dtype and result['trust']=='user_declared_unverified'
    assert result['processing_note']==spec['processing_note']


def test_forged_verified_marker_is_rejected(tmp_path):
    spec=reads(tmp_path);spec['trust']='platform_verified'
    with pytest.raises(InputContractError,match='未知|验证'):validate_input_contract(spec,tmp_path)


def test_mate_names_and_truncated_later_record_are_rejected(tmp_path):
    spec=reads(tmp_path)
    put(tmp_path,'s_R2.fastq','@p1/2\nACGT\n+\nIIII\n@different/2\nTGCA\n+\nIIII\n')
    with pytest.raises(InputContractError,match='配对'):validate_input_contract(spec,tmp_path)
    put(tmp_path,'s_R2.fastq','@p1/2\nACGT\n+\nIIII\n@p2/2\nTGCA\n+\nIII\n')
    with pytest.raises(InputContractError,match='FASTQ'):validate_input_contract(spec,tmp_path)


def test_corrupt_gzip_cannot_pass_by_valid_first_record(tmp_path):
    spec=reads(tmp_path);p=tmp_path/'s_R2.fastq.gz'
    p.write_bytes(gzip.compress((tmp_path/'s_R2.fastq').read_bytes())[:-5]);spec['samples'][0]['read2']=p.name
    with pytest.raises(InputContractError,match='gzip|FASTQ'):validate_input_contract(spec,tmp_path)


def test_contigs_have_cohort_identity_and_unique_nucleotide_records(tmp_path):
    f=put(tmp_path,'contigs.fa','>c1\nACGTN\n>c2\nTGCA\n')
    spec={'kind':'contigs','cohort_id':'joint','fasta':f}
    result=validate_input_contract(spec,tmp_path)
    assert result['data_type']=='assembly_fasta' and result['cohort_id']=='joint'
    assert result['sequence_count']==2 and 'sample_id' not in result
    put(tmp_path,f,'>c1\nACGT\n>c1\nACGT\n')
    with pytest.raises(InputContractError,match='重复'):validate_input_contract(spec,tmp_path)


def test_mag_ids_are_unique_and_not_assigned_to_a_sample(tmp_path):
    f=put(tmp_path,'m.fa','>c1\nACGT\n')
    spec={'kind':'mags','cohort_id':'joint','mags':[{'mag_id':'MAG1','fasta':f}]}
    result=validate_input_contract(spec,tmp_path)
    assert result['data_type']=='mag_fasta' and result['mags'][0]['mag_id']=='MAG1'
    spec['mags'].append({'mag_id':'MAG1','fasta':f})
    with pytest.raises(InputContractError,match='重复'):validate_input_contract(spec,tmp_path)


def matrix(root,unit='fraction'):
    f=put(root,'abundance.tsv','sample_id\tfeature_id\tvalue\na\tsp1\t0.3\nb\tsp1\t0.7\n')
    return {'kind':'matrix','feature_type':'species','unit':unit,'normalization':'按样本总 reads 标准化','table':f}


def test_matrix_has_explicit_units_normalization_and_samples(tmp_path):
    result=validate_input_contract(matrix(tmp_path),tmp_path)
    assert result['unit']=='fraction' and result['sample_ids']==['a','b'] and result['row_count']==2
    assert result['feature_type']=='species'


@pytest.mark.parametrize('change',[{'unit':'guess'},{'normalization':''},{'feature_type':'unknown'}])
def test_matrix_requires_known_semantics(tmp_path,change):
    spec=matrix(tmp_path);spec.update(change)
    with pytest.raises(InputContractError):validate_input_contract(spec,tmp_path)


@pytest.mark.parametrize('value',['NaN','inf','-1','1.1'])
def test_matrix_invalid_fraction_values_rejected(tmp_path,value):
    spec=matrix(tmp_path);put(tmp_path,'abundance.tsv',f'sample_id\tfeature_id\tvalue\na\tsp1\t{value}\n')
    with pytest.raises(InputContractError):validate_input_contract(spec,tmp_path)


def test_matrix_duplicate_keys_and_mixed_units_rejected(tmp_path):
    spec=matrix(tmp_path);put(tmp_path,'abundance.tsv','sample_id\tfeature_id\tvalue\na\tsp1\t0.3\na\tsp1\t0.4\n')
    with pytest.raises(InputContractError,match='重复'):validate_input_contract(spec,tmp_path)
    put(tmp_path,'abundance.tsv','sample_id\tfeature_id\tvalue\tunit\na\tsp1\t0.3\tpercent\n')
    with pytest.raises(InputContractError,match='单位'):validate_input_contract(spec,tmp_path)


def test_input_paths_cannot_escape_or_follow_symlinks(tmp_path):
    spec=reads(tmp_path);spec['samples'][0]['read1']='../outside.fastq'
    with pytest.raises(InputContractError):validate_input_contract(spec,tmp_path)


@pytest.mark.parametrize('unit,value',[('counts','1.00000000000000001'),('fraction','1.00000000000000001'),('fraction','-1e-1000')])
def test_numeric_contract_checks_original_decimal_not_float_rounding(tmp_path,unit,value):
    spec=matrix(tmp_path,unit);put(tmp_path,'abundance.tsv',f'sample_id\tfeature_id\tvalue\na\tsp1\t{value}\n')
    with pytest.raises(InputContractError):validate_input_contract(spec,tmp_path)


def test_header_without_id_is_a_validation_error(tmp_path):
    spec=reads(tmp_path);put(tmp_path,'s_R1.fastq','@\nACGT\n+\nIIII\n')
    with pytest.raises(InputContractError):validate_input_contract(spec,tmp_path)


def test_decompressed_lines_and_fasta_duplicate_index_have_explicit_limits(tmp_path,monkeypatch):
    from backend.app.workflows import input_contracts as module
    monkeypatch.setattr(module,'MAX_SEQUENCE_LINE',8,raising=False)
    spec=reads(tmp_path);put(tmp_path,'s_R1.fastq','@p1/1\n'+'A'*20+'\n+\n'+'I'*20+'\n')
    with pytest.raises(InputContractError,match='上限'):validate_input_contract(spec,tmp_path)
    monkeypatch.setattr(module,'MAX_FASTA_RECORDS',1,raising=False)
    put(tmp_path,'contigs.fa','>a\nACGT\n>b\nACGT\n')
    with pytest.raises(InputContractError,match='上限'):validate_input_contract({'kind':'contigs','cohort_id':'joint','fasta':'contigs.fa'},tmp_path)


@pytest.mark.parametrize('limit,value',[('MAX_SEQUENCE_HEADER',4),('MAX_SEQUENCE_ID',2),('MAX_EXPANDED_BYTES',12),('MAX_FASTA_ID_BYTES',2)])
def test_fasta_gzip_header_expansion_and_id_index_limits(tmp_path,monkeypatch,limit,value):
    from backend.app.workflows import input_contracts as module
    monkeypatch.setattr(module,limit,value)
    (tmp_path/'c.fa.gz').write_bytes(gzip.compress(b'>contig1\nACGT\n'))
    with pytest.raises(InputContractError,match='上限'):
        validate_input_contract({'kind':'contigs','cohort_id':'c','fasta':'c.fa.gz'},tmp_path)


@pytest.mark.parametrize('value',['sNaN','Infinity','-Infinity'])
def test_decimal_nonfinite_values_are_validation_errors(tmp_path,value):
    spec=matrix(tmp_path);put(tmp_path,'abundance.tsv',f'sample_id\tfeature_id\tvalue\na\tsp1\t{value}\n')
    with pytest.raises(InputContractError):validate_input_contract(spec,tmp_path)
