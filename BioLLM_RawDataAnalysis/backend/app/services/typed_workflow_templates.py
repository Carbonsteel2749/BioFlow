"""Declarative starters only; execution remains the shared registry/compiler."""
import copy


SPECS = (
    ('builtin-typed-clean', '外部 clean reads · 去宿主与注释',
     (('input','clean_reads_input'),('host','host_depletion'),('taxonomy','taxonomy'),('function','functional_annotation')),
     (('input','reads','host','reads'),('host','reads','taxonomy','reads'),('host','reads','function','reads')),
     ('声明为 clean 的双端 reads；需填写外部处理说明并确认未经平台验证。',),
     ('去宿主双端 reads 与 metrics', '物种与功能节点标准表及来源记录')),
    ('builtin-typed-host-removed', '外部去宿主 reads · 物种与功能',
     (('input','host_removed_reads_input'),('taxonomy','taxonomy'),('function','functional_annotation')),
     (('input','reads','taxonomy','reads'),('input','reads','function','reads')),
     ('声明为 host_removed 的双端 reads；需填写外部处理说明并确认未经平台验证。',),
     ('物种与功能节点标准表及来源记录',)),
    ('builtin-typed-contigs', '外部 contigs · 分箱与筛选',
     (('contigs','contigs_input'),('reads','host_removed_reads_input'),('binning','binning'),('refinement','bin_refinement')),
     (('contigs','assembly','binning','assembly'),('reads','reads','binning','reads'),('binning','bins','refinement','bins')),
     ('核苷酸 FASTA 与队列 ID。', '匹配同一队列的 host_removed 双端 reads；若需 clean 伴随输入，可在设计器按登记端口修改并发布。'),
     ('候选 bins', '按阈值筛选的 MAG 与质量证据；不等同于质量认证')),
    ('builtin-typed-mag', '外部 MAG · 已登记注释',
     (('input','mag_input'),('annotation','bin_annotation')),
     (('input','mags','annotation','mags'),),
     ('核苷酸 FASTA 集合、唯一 MAG ID 与队列 ID；不将 MAG 独占归属样本，不支持外部 MAG 定量。',),
     ('MAG 分类与功能注释及来源记录',)),
    ('builtin-typed-matrix', '外部丰度矩阵 · 描述性汇总',
     (('input','abundance_input'),('summary','matrix_summary')),
     (('input','matrix','summary','matrix'),),
     ('已登记 canonical 长表或精确识别的 BioFlow 富表；明确选择特征、单位与标准化。',),
     ('描述性汇总表、来源记录及可用图表；coverage 不求和，不补零',)),
)


def typed_template_records(registry):
    records=[]
    for tid,name,nodes,edges,requirements,outputs in SPECS:
        records.append({'template_id':tid,'name':name,
            'description':'使用已有登记方法；工具与数据库仍须预检。输出为节点产物与受保护的完整归档，不含旧 CSV REPORT。',
            'input_requirements':list(requirements),'expected_outputs':list(outputs),
            'workflow':{'schema_version':'1.0','nodes':[
                {'id':nid,'type':kind,'parameters':{k:copy.deepcopy(v['default']) for k,v in registry[kind].parameters_schema.items() if 'default' in v}}
                for nid,kind in nodes],
                'edges':[{'id':f'e{i:02d}','source_node':a,'source_port':ap,'target_node':b,'target_port':bp}
                    for i,(a,ap,b,bp) in enumerate(edges,1)],'accepted_risks':[]}})
    return records
