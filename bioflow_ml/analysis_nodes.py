"""Typed microbiome table operations and study-design checks; no implicit count conversion."""
import csv
import json
import math
from pathlib import Path
import re
import sys

UNITS=['counts','estimated_counts','relative_fraction','relative_percent','cpm','tpm','clr','distance']
def read(path):
    import pandas as pd
    path=Path(path)
    if path.stat().st_size>64*1024**2:raise ValueError('该入口单表上限 64 MiB；请使用经资源配置的大数据入口')
    sep='\t' if path.suffix=='.tsv' else ','
    with path.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.reader(f,delimiter=sep);header=next(reader)
        if any(row and len(row)!=len(header) for row in reader):raise ValueError('CSV/TSV 行列数不一致')
    if not header or header[0]!='sample_id' or len(set(header))!=len(header) or any(not x.strip() or any(c in x for c in '\r\n\x00') for x in header):raise ValueError('首列须为 sample_id，列名不得重复、为空或含控制字符')
    frame=pd.read_csv(path,sep=sep,dtype=str,keep_default_na=False).set_index('sample_id')
    if frame.empty or frame.index.has_duplicates or any(not str(x).strip() or str(x)!=str(x).strip() or any(c in str(x) for c in '\r\n\x00') for x in frame.index):raise ValueError('样本 ID 必须唯一、非空且无首尾空格或控制字符，数据不能为空')
    if frame.size>5_000_000:raise ValueError('单节点最多 500 万个数值单元格')
    return frame

def table(path,unit):
    import numpy as np
    from bioflow_ml.microbiome_analysis._validation import validate_table
    if unit not in UNITS:raise ValueError('必须明确登记输入单位')
    frame=read(path).apply(__import__('pandas').to_numeric,errors='raise')
    frame=validate_table(frame,nonnegative=unit!='clr')
    if unit=='counts' and ((frame.to_numpy()%1!=0).any() or (frame.to_numpy()>2**53).any()):raise ValueError('counts 必须为可精确表示的非负整数，禁止由相对丰度取整伪造计数')
    if unit in ['relative_fraction','relative_percent']:
        limit=1 if unit=='relative_fraction' else 100
        if (frame.sum(axis=1)>limit+1e-6).any():raise ValueError('相对丰度总和超出声明单位范围')
    if unit=='distance':
        from bioflow_ml.microbiome_analysis.diversity import _validate_distance
        frame=_validate_distance(frame)
        if (frame.to_numpy()<0).any():raise ValueError('距离不能为负数')
    elif unit!='clr' and (frame.sum(axis=1)<=0).any():raise ValueError('样本总丰度不能为零')
    return frame

def columns(value):
    values=[v.strip() for v in value.split(',') if v.strip()]
    if len(set(values))!=len(values) or any(not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',v) for v in values):raise ValueError('变量名须为唯一的字母开头英文列名，不允许 R 表达式')
    return values

def design(frame,metadata,p,inferential=True,subject_fixed=False):
    import numpy as np
    import pandas as pd
    meta=read(metadata)
    if set(meta.index)!=set(frame.index):raise ValueError('元数据和分析表的样本 ID 必须完全匹配，不能静默丢样本')
    reordered=not meta.index.equals(frame.index);meta=meta.loc[frame.index]
    group=p['group_column'];numeric=columns(p.get('numeric_covariates',''));categorical=columns(p.get('categorical_covariates',''))
    batch=p.get('batch_column','');subject=p.get('subject_column','');time=p.get('time_column','')
    if batch:categorical.append(batch)
    required=[group,*numeric,*categorical]+([subject] if p.get('design','independent')!='independent' else [])+([time] if time else [])
    if any(not re.fullmatch('[A-Za-z][A-Za-z0-9_]*',v) for v in required):raise ValueError('研究设计字段名无效')
    if len(set([group,*numeric,*categorical]))!=len([group,*numeric,*categorical]):raise ValueError('模型变量重复，或分组变量又被当作协变量')
    for name in required:
        if name not in meta or meta[name].isin(['','NA','NaN','null']).any():raise ValueError('缺少字段或存在缺失值: '+name)
        if meta[name].map(lambda value:any(c in str(value) for c in '\r\n\x00')).any():raise ValueError('研究设计字段不能包含控制字符: '+name)
    for name in numeric:
        meta[name]=pd.to_numeric(meta[name],errors='raise')
        if not np.isfinite(meta[name]).all():raise ValueError('协变量不能含非有限数值')
    levels=sorted(meta[group].unique())
    if inferential and len(levels)<2:raise ValueError('比较分析至少需要两组')
    subject_present=subject and subject in meta
    if p.get('design','independent')=='independent' and subject_present and meta[subject].duplicated().any() and inferential:raise ValueError('存在同一个体重复测量，请声明配对/重复测量设计')
    if p.get('design') in ['paired','repeated']:
        if not subject_present:raise ValueError('配对或重复测量分析必须有个体标识')
        if time and meta.duplicated([subject,time]).any():raise ValueError('同一个体的时间点重复，不能自动当作独立重复')
        if (meta.groupby(subject).size()<2).any():raise ValueError('存在只有一次测量的个体，请先明确处理不完整配对')
    n={g:int(meta.loc[meta[group]==g,subject].nunique() if subject_present else (meta[group]==g).sum()) for g in levels}
    if inferential and any(v<p.get('min_replicates',3) for v in n.values()):raise ValueError('每组有效独立重复未达到登记下限: '+json.dumps(n))
    covars=[group,*numeric,*categorical]+([subject] if subject_fixed and subject_present else [])
    matrix=pd.get_dummies(meta[covars],columns=[group,*categorical]+([subject] if subject_fixed and subject_present else []),drop_first=True,dtype=float)
    matrix.insert(0,'Intercept',1.)
    if inferential and (np.linalg.matrix_rank(matrix.to_numpy(dtype=float))<matrix.shape[1] or matrix.shape[0]<=matrix.shape[1]):raise ValueError('模型混淆、共线或没有剩余自由度；请核对批次、分组及协变量')
    reference=p.get('reference','') or levels[0]
    if reference not in levels or ',' in reference:raise ValueError('参考组不存在或含不支持的逗号')
    return meta,{'metadata_reordered_by_id':reordered,'group_levels':levels,'reference':reference,'effective_replicates':n,'fixed_terms':covars,'numeric_covariates':numeric,'categorical_covariates':categorical,'design':p.get('design','independent')}

def validate(plan):
    node=plan['node_type'];p=plan['parameters'];i=plan['inputs']
    if node.startswith('amplicon_'):
        from bioflow_ml.amplicon_nodes import validate as validate_amplicon
        return validate_amplicon(plan)
    if node=='analysis_figure':
        if Path(i['table']).stat().st_size>64*1024**2:raise ValueError('绘图输入超过 64 MiB')
        frame=__import__('pandas').read_csv(i['table'],sep='\t' if i['table'].endswith('.tsv') else ',',dtype={'feature':str,'term':str,'sample_id':str})
        if frame.empty:raise ValueError('绘图输入不能为空')
        if frame.size>5_000_000 or not p['unit'].strip():raise ValueError('绘图数据过大或未声明单位')
        required={'effect':['feature','term','effect','ci_lower','ci_upper','unit'],'diversity':['sample_id',p['value_column']],'ordination':['sample_id',p['x_column'],p['y_column']],'composition':['sample_id'],'heatmap':['sample_id']}[p['kind']]
        if not set(required).issubset(frame):raise ValueError('图件缺少真实结果字段: '+str(required))
        if p['kind'] in ['composition','heatmap']:table(i['table'],p['unit'])
        if p['kind']=='composition' and p['unit'] in ['clr','distance']:raise ValueError('组成图需要非负丰度单位')
        if p['kind'] in ['diversity','ordination']:
            import numpy as np
            fields=[p['value_column']] if p['kind']=='diversity' else [p['x_column'],p['y_column']]
            if frame.sample_id.isna().any() or frame.sample_id.duplicated().any() or not np.isfinite(frame[fields].apply(__import__('pandas').to_numeric,errors='raise').to_numpy()).all():raise ValueError('样本点必须有唯一 ID 和有效数值，不能静默隐藏缺失/无穷值')
        if p['kind']=='effect' and frame['unit'].nunique()!=1:raise ValueError('效应图不能混合不同单位/模型尺度')
        if p['kind']=='effect' and str(frame['unit'].iloc[0])!=p['unit']:raise ValueError('效应图单位必须与统计结果文件中的原始单位一致')
        return {'rows':len(frame),'plot_kind':p['kind'],'input_unit':p['unit'],'numeric_source':'input table only; no LLM-generated data'}
    frame=table(i['table'],p['unit'])
    info={'samples':len(frame),'features':len(frame.columns),'unit':p['unit']}
    if node in ['analysis_merge','analysis_taxonomy'] and p['unit'] in ['clr','distance']:raise ValueError('丰度合并和分类汇总需要非负丰度，不能直接汇总 CLR 或距离')
    if node=='analysis_taxonomy':
        import pandas as pd
        if not p['taxonomy_system'].strip() or not p['taxonomy_release'].strip():raise ValueError('分类系统及参考版本必须明确填写')
        tax=pd.read_csv(i['taxonomy'],sep='\t' if i['taxonomy'].endswith('.tsv') else ',',dtype=str,keep_default_na=False)
        ranks=['domain','phylum','class','order','family','genus','species'];required=['feature_id',*ranks[:ranks.index(p['rank'])+1]]
        if not set(required).issubset(tax) or tax.feature_id.duplicated().any() or not set(frame.columns).issubset(set(tax.feature_id)):raise ValueError('分类映射缺失、重复或缺少父级谱系，不能静默丢弃特征')
    if node=='analysis_merge':
        other=table(i['other_table'],p['other_unit'])
        if p['unit']!=p['other_unit']:raise ValueError('不能合并不同单位的丰度表')
        if set(frame.index)&set(other.index):raise ValueError('样本 ID 重复，不能静默平均或覆盖')
        if set(frame.columns)!=set(other.columns) and not p['missing_features_zero']:raise ValueError('特征集合不同；只有确认未列出特征等同零值时才能显式补零合并')
    if node in ['analysis_metadata','analysis_group_test','vegan_permanova','vegan_betadisper','ancombc2','maaslin2','analysis_association']:
        model_parameters=dict(p)
        time=p.get('time_column','')
        if node in ['vegan_permanova','ancombc2','maaslin2'] and time and time not in [p['group_column'],*columns(p['numeric_covariates']),*columns(p['categorical_covariates'])]:model_parameters['categorical_covariates']=','.join([*columns(p['categorical_covariates']),time])
        meta,details=design(frame,i['metadata'],model_parameters,node!='analysis_metadata',subject_fixed=node=='vegan_permanova' and p['design']!='independent');info.update(details)
        if node in ['vegan_betadisper','analysis_association'] and p['design']!='independent':raise ValueError('该适配首版仅支持独立样本，不能将重复测量当作独立样本')
        if node=='vegan_permanova' and p['design']!='independent':
            if not all(x[p['group_column']].nunique()>1 for _,x in meta.groupby(p['subject_column'])):raise ValueError('个体内分组不变化，不能用个体内置换检验组间效应；需要整组置换适配')
        if node=='analysis_group_test':
            if p['numeric_covariates'] or p['categorical_covariates'] or p['batch_column']:raise ValueError('简单组间检验不能调整协变量，请使用 ANCOM-BC2/MaAsLin2')
            if len(details['group_levels'])!=2:raise ValueError('当前简单检验节点只支持两组')
            if p['design']=='repeated':raise ValueError('多时间点重复测量请使用多变量模型')
            if p['design']=='paired' and not all(len(x)==2 and x[p['group_column']].nunique()==2 for _,x in meta.groupby(p['subject_column'])):raise ValueError('配对检验要求每个个体恰好一对完整的两组样本')
    if node in ['ancombc2','analysis_rarefaction'] and p['unit']!='counts':raise ValueError('该方法要求原始整数计数，不接受相对丰度或估计计数')
    if node=='maaslin2' and p['unit'] in ['clr','distance']:raise ValueError('当前 MaAsLin2 适配使用非负丰度与 LOG 模型，不接受 CLR 或距离')
    if node=='vegan_betadisper' and (p['numeric_covariates'] or p['categorical_covariates'] or p['batch_column']):raise ValueError('离散度节点不支持协变量调整，不能静默忽略指定协变量')
    if node=='analysis_association' and not columns(p['numeric_covariates']):raise ValueError('关联节点至少指定一个数值协变量')
    if node=='analysis_association' and (p['categorical_covariates'] or p['batch_column'] or p['time_column']):raise ValueError('简单 Spearman 关联不调整批次、时间或分类协变量，请使用多变量模型')
    if node=='analysis_alpha' and p['metric']=='chao1' and p['unit']!='counts':raise ValueError('Chao1 必须使用原始计数')
    if node in ['vegan_permanova','vegan_betadisper'] and p['unit']!='distance':raise ValueError('该节点明确接收已计算的距离矩阵')
    if node=='analysis_transform':
        if p['method']=='clr' and p['unit']=='clr':raise ValueError('不能重复 CLR 转换')
        if p['method']!='clr' and p['unit'] in ['clr','distance']:raise ValueError('该单位不支持丰度归一化')
    if node in ['analysis_alpha','analysis_beta','analysis_filter'] and p['unit'] in ['clr','distance']:raise ValueError('该节点需要非负丰度表')
    return info

def execute(plan,out):
    import numpy as np
    import pandas as pd
    from bioflow_ml.microbiome_analysis.compositional import clr_transform
    from bioflow_ml.microbiome_analysis.diversity import alpha_diversity,beta_distance
    from bioflow_ml.microbiome_analysis.differential import benjamini_hochberg
    from bioflow_ml.microbiome_analysis.association import spearman_association
    info=validate(plan);p=plan['parameters'];i=plan['inputs'];node=plan['node_type'];out=Path(out)
    if node=='analysis_figure':
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        raw=pd.read_csv(i['table'],sep='\t' if i['table'].endswith('.tsv') else ',',dtype={'feature':str,'term':str,'sample_id':str});raw.to_csv(out/'figure-source.csv',index=False)
        fig,ax=plt.subplots(figsize=(8,5));kind=p['kind']
        if kind=='effect':
            numeric=raw[['effect','ci_lower','ci_upper']].apply(pd.to_numeric,errors='raise');valid=np.isfinite(numeric).all(axis=1)
            raw.loc[~valid].to_csv(out/'not-estimable.csv',index=False);data=numeric.loc[valid];labels=raw.loc[valid,'feature'].astype(str)+' | '+raw.loc[valid,'term'].astype(str)
            if data.empty or (data.ci_lower>data.effect).any() or (data.ci_upper<data.effect).any():raise ValueError('没有有效置信区间或置信区间顺序错误，不能编造误差线')
            if len(data)>40:raise ValueError('效应图最多 40 行，请先显式选择要展示的结果，不能静默截取')
            pos=np.arange(len(data));ax.errorbar(data.effect,pos,xerr=[data.effect-data.ci_lower,data.ci_upper-data.effect],fmt='o',capsize=3);ax.set_yticks(pos,labels);ax.axvline(0,color='grey',linestyle='--');ax.set_xlabel(str(raw['unit'].iloc[0]))
        elif kind=='diversity':
            values=pd.to_numeric(raw[p['value_column']],errors='raise');ax.scatter(np.arange(len(raw)),values);ax.set_xticks(np.arange(len(raw)),raw.sample_id,rotation=90);ax.set_ylabel(p['value_column'])
        elif kind=='ordination':
            ax.scatter(pd.to_numeric(raw[p['x_column']]),pd.to_numeric(raw[p['y_column']]));ax.set_xlabel(p['x_column']);ax.set_ylabel(p['y_column'])
            for _,row in raw.iterrows():ax.annotate(str(row.sample_id),(row[p['x_column']],row[p['y_column']]),fontsize=7)
        else:
            data=raw.set_index('sample_id').apply(pd.to_numeric,errors='raise')
            if not np.isfinite(data.to_numpy()).all():raise ValueError('绘图数值必须有限')
            if kind=='heatmap':
                if len(data)>40 or len(data.columns)>30:raise ValueError('热图超过 40 样本/30 特征，请先显式选择或汇总')
                fig.colorbar(ax.imshow(data,aspect='auto'),ax=ax,label=p['unit']);ax.set_yticks(range(len(data)),data.index);ax.set_xticks(range(len(data.columns)),data.columns,rotation=90);ax.set_xlabel('Feature ID');ax.set_ylabel('Sample ID')
            else:
                if len(data)>40 or len(data.columns)>30:raise ValueError('组成图超过 40 样本/30 特征，请先显式汇总或选择特征')
                if (data.to_numpy()<0).any() or (data.sum(axis=1)<=0).any():raise ValueError('组成图必须使用非负且总量大于零的数据')
                data.plot.bar(stacked=True,ax=ax);ax.set_ylabel(p['unit'])
        fig.tight_layout()
        for suffix in ['png','svg','pdf']:fig.savefig(out/('figure.'+suffix),dpi=300)
        plt.close(fig);(out/'analysis-methods.json').write_text(json.dumps(info,indent=2));return
    frame=table(i['table'],p['unit'])
    if node=='analysis_merge':
        other=table(i['other_table'],p['other_unit']);pd.concat([frame,other]).fillna(0).to_csv(out/'abundance.csv');info['missing_features_zero']=p['missing_features_zero']
    elif node=='analysis_taxonomy':
        taxonomy=pd.read_csv(i['taxonomy'],sep='\t' if i['taxonomy'].endswith('.tsv') else ',',dtype=str,keep_default_na=False)
        ranks=['domain','phylum','class','order','family','genus','species'];selected=ranks[:ranks.index(p['rank'])+1]
        if 'feature_id' not in taxonomy or taxonomy.feature_id.duplicated().any() or not set(selected).issubset(taxonomy):raise ValueError('分类映射需要唯一 feature_id 和目标级别之前的全部分类列')
        taxonomy=taxonomy.set_index('feature_id')
        if not set(frame.columns).issubset(taxonomy.index):raise ValueError('存在没有分类映射的特征；必须显式提供未分类记录')
        keys=[]
        for feature in frame:
            lineage=[taxonomy.loc[feature,r] for r in selected]
            keys.append(';'.join(value if value else 'unclassified' for value in lineage)+(('::'+feature) if not any(lineage) else ''))
        result=frame.T.groupby(keys,sort=True).sum().T;result.to_csv(out/'abundance.csv');pd.DataFrame({'feature_id':frame.columns,'aggregate_id':keys}).to_csv(out/'taxonomy_mapping.csv',index=False)
    elif node=='analysis_metadata':read(i['metadata']).loc[frame.index].to_csv(out/'metadata.csv')
    elif node=='analysis_filter':
        mask=((frame>=p['detection_threshold']).sum(axis=0)/len(frame)>=p['min_prevalence'])&(frame.mean(axis=0)>=p['min_mean_abundance'])
        result=frame.loc[:,mask]
        if result.empty or (result.sum(axis=1)<=0).any():raise ValueError('过滤后没有特征或出现全零样本，请调整明确的过滤阈值')
        result.to_csv(out/'abundance.csv');pd.DataFrame({'feature':frame.columns,'retained':mask.to_numpy()}).to_csv(out/'filter_decisions.csv',index=False)
        info['reclosed_after_filter']=False
    elif node=='analysis_transform':
        if p['method']=='clr':result=clr_transform(frame,pseudocount=p['pseudocount']);info['output_unit']='clr'
        else:
            scale={'relative_fraction':1,'relative_percent':100,'cpm':1e6}[p['method']];result=frame.div(frame.sum(axis=1),axis=0)*scale;info['output_unit']=p['method']
        result.to_csv(out/'abundance.csv');info['denominator']='retained feature sample total' if p['method']!='clr' else 'geometric mean after declared pseudocount'
    elif node=='analysis_alpha':alpha_diversity(frame,metrics=(p['metric'],)).to_csv(out/'alpha.csv')
    elif node=='analysis_beta':beta_distance(frame,metric=p['metric'],pseudocount=p['pseudocount']).to_csv(out/'distances.csv')
    elif node=='analysis_association':
        meta=read(i['metadata']).loc[frame.index];variables=columns(p['numeric_covariates'])
        if not variables:raise ValueError('关联节点至少指定一个数值协变量')
        # Existing function, exploratory association only; not an adjusted model.
        spearman_association(frame,meta[variables].apply(pd.to_numeric)).to_csv(out/'associations.csv',index=False)
        info['interpretation']='unadjusted exploratory Spearman association; not causal or direct interaction'
    elif node=='analysis_group_test':
        from scipy import stats
        meta=read(i['metadata']).loc[frame.index];group=p['group_column'];control=info['reference'];case=next(x for x in info['group_levels'] if x!=control);results=[]
        for feature in frame:
            if p['design']=='paired':
                combined=meta[[group,p['subject_column']]].assign(value=frame[feature]).pivot(index=p['subject_column'],columns=group,values='value');a=combined[case].to_numpy();b=combined[control].to_numpy();test=stats.ttest_rel(a,b);delta=a-b;se=stats.sem(delta);df=len(delta)-1
            else:
                a=frame.loc[meta[group]==case,feature].to_numpy();b=frame.loc[meta[group]==control,feature].to_numpy();test=stats.ttest_ind(a,b,equal_var=False);v1=np.var(a,ddof=1)/len(a);v2=np.var(b,ddof=1)/len(b);se=np.sqrt(v1+v2);df=(v1+v2)**2/(v1*v1/(len(a)-1)+v2*v2/(len(b)-1)) if se else 0
            effect=float(np.mean(a)-np.mean(b));half=float(stats.t.ppf(.975,df)*se) if df>0 and se>0 else float('nan');prob=float(test.pvalue) if se>0 else float('nan')
            results.append({'feature':feature,'term':case+' vs '+control,'effect':effect,'ci_lower':effect-half if np.isfinite(half) else None,'ci_upper':effect+half if np.isfinite(half) else None,'p_value':prob if np.isfinite(prob) else None,'unit':'mean difference in '+p['unit'],'method':'paired_t' if p['design']=='paired' else 'welch_t','status':'tested' if np.isfinite(prob) else 'not_testable'})
        result=pd.DataFrame(results);valid=result.p_value.notna();result['q_value']=np.nan
        result.loc[valid,'q_value']=benjamini_hochberg(result.loc[valid,'p_value'].to_numpy());result.to_csv(out/'effects.csv',index=False)
    elif node=='analysis_rarefaction':
        rng=np.random.default_rng(p['seed']);depths=sorted(set(int(x) for x in p['depths'].split(',')));results=[]
        if not depths or min(depths)<1:raise ValueError('稀释深度必须为正整数')
        for sample,row in frame.iterrows():
            counts=row.to_numpy(dtype=np.int64)
            for depth in depths:
                if depth>counts.sum():results.append({'sample_id':sample,'depth':depth,'mean_observed':None,'status':'insufficient_depth'});continue
                draws=[np.count_nonzero(rng.multivariate_hypergeometric(counts,depth)) for _ in range(p['iterations'])]
                results.append({'sample_id':sample,'depth':depth,'mean_observed':float(np.mean(draws)),'status':'computed'})
        pd.DataFrame(results).to_csv(out/'rarefaction.csv',index=False);info['scope']='descriptive rarefaction curve only; original tables are not replaced'
    else:raise ValueError('未知 Python 分析节点')
    (out/'analysis-methods.json').write_text(json.dumps({**info,'node':node,'parameters':p,'real_execution':True},ensure_ascii=False,indent=2))

if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
    if sys.argv[1]=='--preflight':
        try:print(json.dumps({'inspection':validate(json.load(sys.stdin))}))
        except Exception as exc:print(json.dumps({'error':str(exc)}));sys.exit(2)
