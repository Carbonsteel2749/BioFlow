"""File content and semantic contracts shared by typed input registration/execution.

Validation does not promote external processing claims to platform verification.
Paths in a contract are relative to its controlled storage root, never browser paths.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import itertools
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path


class InputContractError(ValueError):
    pass


SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
NUCLEOTIDES = frozenset('ACGTRYSWKMBDHVNacgtryswkmbdhvn')
FEATURE_TYPES = ('species', 'gene_families', 'KO', 'EC', 'pathway_abundance', 'pathway_coverage')
UNITS = ('fraction', 'percent', 'counts', 'CPM', 'RPK', 'RPKM', 'TPM', 'HUMAnN_reported', 'coverage')
READ_STATES = {'raw':'paired_raw_reads', 'clean':'paired_clean_reads', 'host_removed':'paired_host_removed_reads'}
MAX_MATRIX_ROWS = 2_000_000
MAX_SEQUENCE_LINE = 16 * 1024 * 1024
MAX_SEQUENCE_HEADER = 4096
MAX_SEQUENCE_ID = 512
MAX_FASTA_RECORDS = 1_000_000
MAX_FASTA_ID_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 250 * 1024**3
NUMERIC_POLICY = {'significant_digits':28,'min_adjusted_exponent':-100,'max_adjusted_exponent':100,
                  'sum_precision':237,'mean_precision':28,'mean_rounding':'ROUND_HALF_EVEN','plot_values':'finite float display only'}
VALIDATOR_CAPS = {'source_bytes':MAX_EXPANDED_BYTES,'expanded_sequence_bytes':MAX_EXPANDED_BYTES,
    'matrix_rows':MAX_MATRIX_ROWS,'paired_samples':1024,'mags':10000,'fasta_records':MAX_FASTA_RECORDS,
    'sequence_line_bytes':MAX_SEQUENCE_LINE,'sequence_header_bytes':MAX_SEQUENCE_HEADER,
    'sequence_id_bytes':MAX_SEQUENCE_ID,'fasta_id_bytes':MAX_FASTA_ID_BYTES}


def matrix_value(text):
    try:value=Decimal(text)
    except (InvalidOperation,ValueError,TypeError) as e:raise InputContractError('矩阵值不是数值') from e
    if not value.is_finite() or value<0:raise InputContractError('矩阵值必须是非负有限数值')
    digits=list(value.as_tuple().digits)
    while digits and digits[-1]==0:digits.pop()
    if len(digits)>28 or value and not -100<=value.adjusted()<=100:
        raise InputContractError('矩阵数值支持最多 28 位有效数字及非零调整指数 -100..100；不会舍入接受')
    return value


class _SequenceLines:
    """Bound allocations before decompression output reaches a Python string."""
    def __init__(self,handle):self.handle=handle;self.total=0

    def readline(self,limit=None):
        limit=MAX_SEQUENCE_LINE if limit is None else limit
        line=self.handle.readline(limit+3)
        self.total+=len(line)
        if len(line.rstrip('\r\n'))>limit:raise InputContractError('序列单行长度超过校验上限')
        if self.total>MAX_EXPANDED_BYTES:raise InputContractError('序列解压内容超过校验上限')
        return line


def _identifier(value, label):
    if not isinstance(value,str) or not SAFE_ID.fullmatch(value):
        raise InputContractError(label+' 需要有效且非空的标识符')
    return value


def _allowed(obj, names):
    if not isinstance(obj,dict) or set(obj)-set(names):
        raise InputContractError('输入契约存在未知字段；验证身份不能由客户端设置')


def _text(value,label,limit=4000):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise InputContractError(label+' 必须填写，且不能超过 '+str(limit)+' 字符')
    return value.strip()


class _Files:
    def __init__(self,root):
        self.root=Path(root).resolve(strict=True);self.records={};self.stats={}

    def get(self,name,suffixes):
        if not isinstance(name,str) or not name or '\\' in name or Path(name).is_absolute() or any(s in {'..','.'} for s in name.split('/')):
            raise InputContractError('输入文件路径不合法')
        p=self.root/name
        if any(q.is_symlink() for q in [p,*p.parents] if q!=self.root.parent):
            raise InputContractError('输入文件不能为符号链接')
        try:
            resolved=p.resolve(strict=True)
            if not resolved.is_relative_to(self.root) or not resolved.is_file():raise OSError('not contained')
            st=resolved.stat()
            if st.st_size==0:raise InputContractError('输入文件为空')
        except OSError as e:raise InputContractError('输入文件不存在或超出受控目录') from e
        if not name.lower().endswith(suffixes):raise InputContractError('输入文件扩展名与所选类型不符')
        if name not in self.records:
            h=hashlib.sha256()
            with resolved.open('rb') as f:
                for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
            self.records[name]={'path':name,'size_bytes':st.st_size,'sha256':h.hexdigest()}
            self.stats[name]=(st.st_size,st.st_mtime_ns,st.st_ino)
        return resolved

    def finish(self):
        for name,previous in self.stats.items():
            st=(self.root/name).stat()
            if previous!=(st.st_size,st.st_mtime_ns,st.st_ino):raise InputContractError('文件在校验过程中发生变化，请重新导入')
        return [self.records[k] for k in sorted(self.records)]


def _open(path):
    return gzip.open(path,'rt',encoding='ascii',newline='') if path.name.lower().endswith('.gz') else path.open(encoding='ascii',newline='')


def _fastq_records(path,mate):
    try:
        with _open(path) as f:
            f=_SequenceLines(f)
            while True:
                header=f.readline(MAX_SEQUENCE_HEADER)
                if not header:break
                seq=f.readline().rstrip('\r\n');plus=f.readline(MAX_SEQUENCE_HEADER);quality=f.readline().rstrip('\r\n')
                if not header.startswith('@') or not plus.startswith('+') or not seq or not set(seq)<=NUCLEOTIDES or len(seq)!=len(quality) or any(not 33<=ord(q)<=126 for q in quality):
                    raise InputContractError('FASTQ 记录不完整或序列/质量格式错误')
                tokens=header[1:].split()
                if not tokens:raise InputContractError('FASTQ read ID 为空')
                token=tokens[0]
                if len(token)>MAX_SEQUENCE_ID:raise InputContractError('FASTQ read ID 长度超过校验上限')
                if token.endswith(('/1','/2')):
                    if token[-1]!=str(mate):raise InputContractError('FASTQ 配对方向与 R1/R2 不一致')
                    token=token[:-2]
                if not token:raise InputContractError('FASTQ read ID 为空')
                yield token
    except (OSError,EOFError,UnicodeError) as e:raise InputContractError('FASTQ/gzip 全量读取校验失败') from e


def _fasta(path):
    ids=set();current=None;length=0;total=0;id_bytes=0
    try:
        with _open(path) as f:
            lines=_SequenceLines(f)
            while line:=lines.readline():
                line=line.strip()
                if not line:continue
                if line.startswith('>'):
                    if len(line)>MAX_SEQUENCE_HEADER:raise InputContractError('FASTA 标题长度超过校验上限')
                    if current is not None and length==0:raise InputContractError('FASTA 存在空序列')
                    tokens=line[1:].split();current=tokens[0] if tokens else ''
                    id_bytes+=len(current)
                    if len(current)>MAX_SEQUENCE_ID or len(ids)>=MAX_FASTA_RECORDS or id_bytes>MAX_FASTA_ID_BYTES:
                        raise InputContractError('FASTA ID 长度、数量或索引大小超过校验上限')
                    if not current or current in ids:raise InputContractError('FASTA 序列 ID 为空或重复')
                    ids.add(current);length=0
                else:
                    if current is None or not set(line)<=NUCLEOTIDES:raise InputContractError('FASTA 必须为有效核酸序列，不能是蛋白序列')
                    length+=len(line);total+=len(line)
    except (OSError,EOFError,UnicodeError) as e:raise InputContractError('FASTA/gzip 全量读取校验失败') from e
    if not ids or length==0:raise InputContractError('FASTA 不含有效序列')
    return len(ids),total


def _matrix(spec,files):
    feature=spec.get('feature_type');unit=spec.get('unit')
    if feature not in FEATURE_TYPES or unit not in UNITS:raise InputContractError('矩阵特征类型或单位未登记')
    if feature=='pathway_coverage' and unit not in ('coverage','fraction','percent'):raise InputContractError('通路覆盖度必须明确为 coverage/fraction/percent，不可当作丰度')
    if feature!='pathway_coverage' and unit=='coverage':raise InputContractError('覆盖度单位不能用于丰度矩阵')
    normalization=_text(spec.get('normalization'),'标准化说明')
    p=files.get(spec.get('table'),('.tsv','.csv'));samples=set();keys=set();count=0
    try:
        with p.open(encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f,delimiter='\t' if p.suffix.lower()=='.tsv' else ',')
            names=reader.fieldnames or []
            if len(names)!=len(set(names)) or not {'sample_id','feature_id','value'}<=set(names) or set(names)-{'sample_id','feature_id','value','feature_name','unit'}:
                raise InputContractError('矩阵应为长表：sample_id、feature_id、value，可选 feature_name、unit')
            for row in reader:
                count+=1
                if count>MAX_MATRIX_ROWS:raise InputContractError('矩阵超过当前全量校验上限 '+str(MAX_MATRIX_ROWS)+' 行；不会截断使用')
                sample=_identifier(row['sample_id'],'样本 ID');fid=_text(row['feature_id'],'特征 ID',512)
                if None in row or any(v is None for v in row.values()):raise InputContractError('矩阵列数不一致')
                if (sample,fid) in keys:raise InputContractError('矩阵 sample_id + feature_id 重复')
                keys.add((sample,fid));samples.add(sample)
                value=matrix_value(row['value'])
                if unit in ('fraction','coverage') and value>1 or unit=='percent' and value>100:raise InputContractError('矩阵数值超出明确单位允许范围')
                if unit=='counts' and value!=value.to_integral_value():raise InputContractError('counts 必须为整数计数')
                if 'unit' in row and row['unit']!=unit:raise InputContractError('矩阵含不同单位或与声明单位不一致')
    except (UnicodeError,OSError,csv.Error) as e:raise InputContractError('矩阵文件无法完整读取') from e
    if count==0:raise InputContractError('矩阵没有数据行')
    return {'table':spec['table'],'feature_type':feature,'unit':unit,'normalization':normalization,'sample_ids':sorted(samples),'row_count':count,'numeric_policy':dict(NUMERIC_POLICY)}


def validate_input_contract(spec:dict,root:Path)->dict:
    common={'kind','processing_note','confirmed'}
    fields={'reads':{'processing_status','samples'},'contigs':{'cohort_id','fasta'},'mags':{'cohort_id','mags'},'matrix':{'table','feature_type','unit','normalization'}}
    kind=spec.get('kind') if isinstance(spec,dict) else None
    if not isinstance(kind,str) or kind not in fields:raise InputContractError('输入类型未登记')
    _allowed(spec,common|fields[kind]);files=_Files(root)
    result={'schema_version':'1.0','kind':kind,'trust':'external_unverified'}
    if kind=='reads':
        stage=spec.get('processing_status','raw')
        if not isinstance(stage,str) or stage not in READ_STATES:raise InputContractError('reads 处理状态无效')
        if stage!='raw':
            result['processing_note']=_text(spec.get('processing_note'),'处理说明')
            if spec.get('confirmed') is not True:raise InputContractError('外部处理声明必须二次确认')
            result['trust']='user_declared_unverified'
        rows=spec.get('samples')
        if not isinstance(rows,list) or not 1<=len(rows)<=1024:raise InputContractError('需要 1–1024 个配对样本')
        seen=set();result['samples']=[]
        for row in rows:
            _allowed(row,{'sample_id','read1','read2'});sid=_identifier(row.get('sample_id'),'样本 ID')
            if sid in seen:raise InputContractError('样本 ID 重复')
            seen.add(sid);a=files.get(row.get('read1'),('.fastq','.fq','.fastq.gz','.fq.gz'));b=files.get(row.get('read2'),('.fastq','.fq','.fastq.gz','.fq.gz'))
            if a==b or a.stat().st_ino and a.stat().st_ino==b.stat().st_ino:raise InputContractError('R1/R2 不能引用同一个文件')
            pairs=0
            for x,y in itertools.zip_longest(_fastq_records(a,1),_fastq_records(b,2)):
                if x is None or y is None or x!=y:raise InputContractError('FASTQ 配对数量或 read ID 不匹配')
                pairs+=1
            if not pairs:raise InputContractError('FASTQ 文件为空')
            result['samples'].append({**row,'pairs':pairs})
        result.update(data_type=READ_STATES[stage],processing_status=stage)
    elif kind=='contigs':
        cohort=_identifier(spec.get('cohort_id'),'队列 ID');p=files.get(spec.get('fasta'),('.fa','.fna','.fasta','.fa.gz','.fna.gz','.fasta.gz'));count,bases=_fasta(p)
        result.update(data_type='assembly_fasta',cohort_id=cohort,fasta=spec['fasta'],sequence_count=count,bases=bases)
    elif kind=='mags':
        cohort=_identifier(spec.get('cohort_id'),'队列 ID');rows=spec.get('mags')
        if not isinstance(rows,list) or not 1<=len(rows)<=10000:raise InputContractError('MAG 清单数量无效')
        seen=set();paths=set();mags=[]
        for row in rows:
            _allowed(row,{'mag_id','fasta'});mid=_identifier(row.get('mag_id'),'MAG ID')
            if mid in seen or row.get('fasta') in paths:raise InputContractError('MAG ID 或文件重复')
            seen.add(mid);paths.add(row.get('fasta'));p=files.get(row.get('fasta'),('.fa','.fna','.fasta','.fa.gz','.fna.gz','.fasta.gz'));count,bases=_fasta(p)
            mags.append({**row,'sequence_count':count,'bases':bases})
        result.update(data_type='mag_fasta',cohort_id=cohort,mags=mags)
    else:
        result.update(data_type='abundance_matrix',**_matrix(spec,files))
    result['files']=files.finish()
    serialized=__import__('json').dumps(result,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
    result['manifest_sha256']=hashlib.sha256(serialized).hexdigest()
    return result
