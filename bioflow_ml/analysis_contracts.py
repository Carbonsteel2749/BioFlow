"""Versioned input contracts for the first general-statistics increment."""
def s(default=''):return {'type':'string','default':default}
def n(default,minimum=0,maximum=1):return {'type':'number','default':default,'minimum':minimum,'maximum':maximum}
def integer(default,minimum=1,maximum=9999):return {'type':'integer','default':default,'minimum':minimum,'maximum':maximum}
UNIT={'type':'string','required':True,'enum':['counts','estimated_counts','relative_fraction','relative_percent','cpm','tpm','clr','distance']}
DESIGN={'group_column':s('group'),'subject_column':s('subject_id'),'time_column':s(''),'batch_column':s(''),'numeric_covariates':s(''),'categorical_covariates':s(''),'reference':s(''),'design':{'type':'string','default':'independent','enum':['independent','paired','repeated']},'min_replicates':integer(3,2,100)}
CONTRACTS={}
def add(name,outputs,params=None,metadata=False,r=False):
    CONTRACTS[name]={'inputs':['table']+(['metadata'] if metadata else []),'tools':['analysis_python']+(['R_statistics'] if r else []),'worker_python':'analysis_python','parameters':{'unit':UNIT,**(DESIGN if metadata else {}),**(params or {})},'outputs':outputs+['analysis-methods.json'],'analysis':True,'method_version':'0.3.0','resource_requirements':{'table_limit_mib':64,'max_cells':5000000,'threads':2,'runtime_seconds':3600},'applicability':'Explicit units, matched IDs and study-design inspection required; no implicit rarefaction'}
add('analysis_metadata',['metadata.csv'],metadata=True)
add('analysis_merge',['abundance.csv'],{'other_unit':UNIT,'missing_features_zero':{'type':'boolean','default':False}});CONTRACTS['analysis_merge']['inputs'].append('other_table')
add('analysis_taxonomy',['abundance.csv','taxonomy_mapping.csv'],{'rank':{'type':'string','default':'genus','enum':['domain','phylum','class','order','family','genus','species']},'taxonomy_system':{'type':'string','required':True},'taxonomy_release':{'type':'string','required':True}});CONTRACTS['analysis_taxonomy']['inputs'].append('taxonomy')
add('analysis_filter',['abundance.csv','filter_decisions.csv'],{'detection_threshold':n(.000001,0,1e12),'min_prevalence':n(.1),'min_mean_abundance':n(0,0,1e12)})
add('analysis_transform',['abundance.csv'],{'method':{'type':'string','default':'relative_fraction','enum':['relative_fraction','relative_percent','cpm','clr']},'pseudocount':n(.000001,1e-12,100)})
add('analysis_alpha',['alpha.csv'],{'metric':{'type':'string','default':'shannon','enum':['shannon','simpson','observed_features','chao1']}})
add('analysis_beta',['distances.csv'],{'metric':{'type':'string','default':'braycurtis','enum':['braycurtis','jaccard','aitchison']},'pseudocount':n(.000001,1e-12,100)})
add('analysis_group_test',['effects.csv'],metadata=True)
add('analysis_association',['associations.csv'],metadata=True)
add('analysis_rarefaction',['rarefaction.csv'],{'depths':s('100,500,1000'),'iterations':integer(20,1,200),'seed':integer(42,0,2147483647)})
for name in ['vegan_permanova','vegan_betadisper']:add(name,['effects.csv','R-session.txt'],{'permutations':integer(999,19,9999),'seed':integer(42,0,2147483647)},metadata=True,r=True)
for name in ['ancombc2','maaslin2']:add(name,['effects.csv','feature-id-map.csv','R-session.txt'],{'seed':integer(42,0,2147483647),'min_prevalence':n(.1),'fdr_alpha':n(.05,.00001,1)},metadata=True,r=True)
add('analysis_figure',['figure.png','figure.svg','figure.pdf','figure-source.csv'],{'unit':{'type':'string','required':True},'kind':{'type':'string','required':True,'enum':['composition','diversity','ordination','heatmap','effect']},'value_column':s('shannon'),'x_column':s('PCoA1'),'y_column':s('PCoA2')})

SHARED={'analysis_alpha':'microbiome_analysis.diversity.alpha_diversity','analysis_beta':'microbiome_analysis.diversity.beta_distance','analysis_transform':'microbiome_analysis.compositional.clr_transform (CLR mode)','analysis_association':'microbiome_analysis.association.spearman_association'}
for name,contract in CONTRACTS.items():
    contract.update(node_id=name,entrypoint='scripts/statistics_worker.R' if 'R_statistics' in contract['tools'] else 'bioflow_ml.analysis_nodes.execute',algorithm_reference=SHARED.get(name,name),version_binding='immutable containing Skill revision plus library-file-hashes.json',input_format='CSV/TSV; sample_id first column; metadata matched by sample_id',output_format='CSV tables, JSON methods; figures additionally PNG/SVG/PDF',unit_contract='Explicit input unit parameter; generated metadata records transforms; statistical effect units are emitted per method',log_contract='central task log, task.json and method/session files',cache_invalidation=['fixed Skill/algorithm source hashes','input content hashes','parameters including seed/design/units','tool executable and environment registration','database version and content checksums'])
