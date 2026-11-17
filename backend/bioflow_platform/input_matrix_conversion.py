"""Explicit rich-table adapters; originals are never modified or normalized."""
import csv
import io
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

SPECIES=set('sample_id taxid taxonomy taxonomy_rank taxonomy_system read_count_or_estimated_reads abundance abundance_unit classification_source database_profile database_release'.split())
FUNCTION=set('sample_id function_source function_namespace function_id function_name function_type abundance abundance_unit relative_abundance normalization_method evidence_level analysis_scope database_profile database_release'.split())
CANONICAL={'sample_id','feature_id','value','feature_name','unit'}
KINDS={'gene_family':('UniRef90','gene_families'),'ko':('KO','KO'),'ec':('EC','EC'),
       'pathway_abundance':('MetaCyc','pathway_abundance'),'pathway_coverage':('MetaCyc','pathway_coverage')}
COVERAGE_DOC='https://github.com/biobakery/humann/blob/v3.9/readme.md#3-pathway-coverage-file'


class ConversionError(ValueError):pass


def convert_matrix(folder,spec,check_space,max_rows=2000000,max_bytes=250*1024**3):
    source=folder/spec['table']
    try:
        with source.open(encoding='utf-8-sig',newline='') as handle:
            reader=csv.DictReader(handle,delimiter='\t' if source.suffix.lower()=='.tsv' else ',')
            names=reader.fieldnames or [];fields=set(names)
            if len(names)!=len(fields):raise ConversionError('矩阵列名重复')
            if {'sample_id','feature_id','value'}<=fields<=CANONICAL:return None
            if fields not in (SPECIES,FUNCTION):raise ConversionError('仅支持 canonical 长表或明确的 BioFlow 物种/功能完整列模式；不推断宽表或原始 HUMAnN 两列表')
            species=fields==SPECIES
            counts=dict(input_rows=0,included_rows=0,excluded_diagnostics=0,excluded_strata=0)
            context=None;alias=False;size=0
            target=folder/'derived/canonical.tsv';target.parent.mkdir(exist_ok=False)
            with target.open('xb') as output:
                def write(values):
                    nonlocal size
                    text=io.StringIO();csv.writer(text,delimiter='\t',lineterminator='\n').writerow(values)
                    data=text.getvalue().encode('utf-8');size+=len(data)
                    if size>max_bytes:raise ConversionError('转换输出超过文件大小上限')
                    check_space(len(data));output.write(data)
                write(['sample_id','feature_id','value','feature_name','unit'])
                for row in reader:
                    counts['input_rows']+=1
                    if counts['input_rows']>max_rows:raise ConversionError('矩阵超过 2000000 行转换上限；不会截断使用')
                    if None in row or any(v is None for v in row.values()):raise ConversionError('矩阵列数不一致')
                    if any(not row[k].strip() for k in ('database_profile','database_release','sample_id')):raise ConversionError('矩阵缺少样本或数据库上下文')
                    if species:
                        if row['taxonomy_rank']!='species' or row['classification_source']!='Kraken2+Bracken' or row['abundance_unit']!='fraction_of_reads' or row['taxonomy_system'] not in ('NCBI','NCBI taxonomy') or not row['taxid'].isascii() or not row['taxid'].isdigit():
                            raise ConversionError('物种模式需要 species、TaxID、taxonomy_system、Kraken2+Bracken 及 fraction_of_reads')
                        if spec['feature_type']!='species' or spec['unit']!='fraction' or spec['normalization']!='none':raise ConversionError('物种转换请选择 species、fraction 和 normalization=none；数值不变')
                        ctx=tuple(row[k] for k in ('taxonomy_system','database_profile','database_release'))
                        fid=quote(row['taxonomy_system'],safe='')+':'+row['taxid'];name=row['taxonomy']
                    else:
                        kind=row['function_type'];expected=KINDS.get(kind)
                        if not expected or row['function_source']!='HUMAnN' or row['evidence_level']!='reads' or (row['function_namespace'],spec['feature_type'])!=expected:
                            raise ConversionError('功能来源、命名空间、类型或 reads 证据不匹配')
                        unit=row['abundance_unit'];alias=kind=='pathway_coverage' and unit=='HUMAnN_reported' and row['normalization_method']=='HUMAnN raw output'
                        if row['normalization_method']!='HUMAnN raw output' or unit not in ('HUMAnN_reported','coverage') or unit=='coverage' and kind!='pathway_coverage':
                            raise ConversionError('未识别的 BioFlow HUMAnN 单位或标准化方法；请提供明确 canonical 长表')
                        effective='coverage' if alias else unit
                        if effective!=spec['unit'] or row['normalization_method']!=spec['normalization'] or not row['normalization_method'].strip():raise ConversionError('请选择与来源一致的单位和标准化说明；旧版 HUMAnN 通路覆盖度须明确选择 coverage')
                        if kind=='pathway_coverage' and effective!='coverage':raise ConversionError('通路覆盖度转换只接受明确 coverage 语义')
                        ctx=tuple(row[k] for k in ('function_source','function_namespace','function_type','abundance_unit','normalization_method','database_profile','database_release','evidence_level'))
                        fid=row['function_id'];name=row['function_name']
                        if row['analysis_scope'] not in ('community_total','taxonomic_stratum','stratified'):raise ConversionError('未知 analysis_scope，不能推断 community_total')
                    if context is not None and ctx!=context:raise ConversionError('矩阵混合数据库、分类系统、命名空间、单位或标准化上下文；请分开导入')
                    context=ctx
                    if not species:
                        base=fid.split('|',1)[0].split(':',1)[0]
                        if base in {'UNMAPPED','READS_UNMAPPED','UNGROUPED','UNINTEGRATED','UniRef90_unknown','UniRef50_unknown'}:
                            counts['excluded_diagnostics']+=1;continue
                        if '|' in fid or row['analysis_scope']!='community_total':counts['excluded_strata']+=1;continue
                    if not fid.strip() or not name.strip():raise ConversionError('缺少命名生物特征，不会按零生物信号导入')
                    try:value=Decimal(row['abundance'])
                    except InvalidOperation as exc:raise ConversionError('矩阵 abundance 不是数值') from exc
                    if not value.is_finite() or value<0 or spec['unit'] in ('fraction','coverage') and value>1:raise ConversionError('矩阵 abundance 超出明确单位范围')
                    write([row['sample_id'],fid,row['abundance'],name,spec['unit']]);counts['included_rows']+=1
            if not counts['included_rows']:raise ConversionError('排除诊断及分层行后无命名 community-total 生物特征')
            rule=('fraction_of_reads -> fraction; taxonomy_system:TaxID; values unchanged; normalization none' if species else
                  'diagnostic ID first, then taxonomic strata, then named community_total; mutually exclusive; values unchanged; no aggregation')
            if alias:rule+='; legacy HUMAnN/MetaCyc pathway_coverage HUMAnN_reported -> coverage semantic alias; explicit user coverage choice; '+COVERAGE_DOC
            spec['table']='derived/canonical.tsv'
            return dict(schema='bioflow.rich.species.v1' if species else 'bioflow.rich.functional.v1',rule=rule,**counts,unit=spec['unit'],normalization=spec['normalization'],output_file=spec['table'])
    except (UnicodeError,csv.Error) as exc:raise ConversionError('矩阵无法按完整 UTF-8 CSV/TSV 模式读取') from exc
