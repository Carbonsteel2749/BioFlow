/* Shared view model: display stages are not fabricated execution stages. */
(function (scope) {
  const stages = {
    data: ['validate'], qc: ['fastqc_raw', 'fastp'], host: ['host_depletion'],
    taxonomy: ['taxonomy'], functional: ['functional_annotation'],
    pathways: ['functional_annotation'], report: ['report'], catalog: []
  };
  const node = (id, label, tool, description, output) => ({id, label, tool, description, output});
  const standard = [
    node('data','数据与样本','Paired-end FASTQ','选择当前项目的数据集，核对 R1 / R2 配对及样本分组。','已校验的样本清单与分组快照'),
    node('qc','质量检查与清洗','FastQC · fastp','先检查原始 reads，再处理接头、低质量碱基和过短序列。','FastQC 报告、fastp HTML / JSON、清洗后 reads'),
    node('host','去除宿主序列','Bowtie2','使用已登记的人类参考索引过滤宿主序列，保留过滤指标。','非宿主 reads 与去宿主统计'),
    node('taxonomy','物种组成','Kraken2 · Bracken','对去宿主 reads 分类并估算物种丰度，与功能分析使用相同输入。','物种丰度表、分类报告与组成 / 多样性图'),
    node('functional','基因功能','HUMAnN','注释群落基因家族，并按配套映射生成 KO / EC 结果。','基因家族、KO、EC 丰度与功能图'),
    node('pathways','代谢通路','HUMAnN · MetaCyc','展示同一次 HUMAnN 计算产生的通路丰度与覆盖度，不另跑一遍。','通路丰度、通路覆盖度与图表'),
    node('report','报告与汇总','MultiQC · BioFlow','汇总阶段产物、图表、运行参数、溯源及初步解读。','综合报告、结果文件、图表与任务归档')
  ];
  const paper = [
    node('data','数据与分组','PRJEB6070 · Zeller 2014','原文纳入标准、诊断分组及测序类型需独立核对；六样本仅作工程测试。','论文样本清单及纳入 / 排除记录'),
    node('qc','质量控制与接头','MOCAT 1.2 · USEARCH 5','原文长度阈值 45 bp、质量阈值 20；接头筛查 e-value 1e-5。','筛查后的高质量 reads'),
    node('host','去宿主','MOCAT · hg19','原文比对长度阈值 45、最低序列一致性 90%；不能与现有 GRCh38 配置混同。','筛查后的非宿主 reads'),
    node('taxonomy','标记基因物种定量','MOCAT · 10 个单拷贝标记','基于 3,496 个参考基因组中的标记基因；比对长度阈值 45，一致性 97%。','标记基因定义的物种丰度'),
    node('catalog','组装与基因目录','SOAP · MetaGeneMark · CD-HIT','组装修订、预测基因、结合外部研究基因去冗余；不是 MAG 分箱流程。','非冗余基因目录与样本基因丰度'),
    node('functional','基因功能注释','BLAST · KEGG v62 · dbCAN','按原文进行 KO 和 CAZy 注释；历史数据库及映射规则尚待接入。','基因注释表、KO / CAZy 功能结果'),
    node('pathways','功能模块汇总','KEGG modules · CAZy','按论文定义汇总功能，不能用 MetaCyc 通路直接代替 KEGG 模块。','论文功能模块与定量依据'),
    node('report','复现核对','来源与差异记录','区分流程运行成功和论文结论复现；不能以六个浅测序样本复现分类器。','方法差异、输出核对与待满足条件')
  ];
  const templates = {
    standard: {name:'标准宏基因组分析',version:'现有工具链',executable:true,nodes:standard},
    paper: {name:'Zeller 2014 · 论文复现',version:'方法设计稿',executable:false,nodes:paper}
  };
  function nodeStatus(task, key) {
    const expected=stages[key]||[], steps=Array.isArray(task?.steps)?task.steps:[];
    const found=expected.map(name=>steps.find(s=>s?.name===name));
    if (!expected.length || found.every(s=>!s)) return 'unknown';
    const values=found.map(s=>s?.status||'unknown');
    for (const state of ['failed','error','running','paused','cancelled','queued','pending']) {
      if(values.includes(state)) return state==='error'?'failed':state;
    }
    if(values.every(s=>['succeeded','completed','cached','skipped'].includes(s))) return values.every(s=>s==='cached')?'cached':values.every(s=>s==='skipped')?'skipped':'succeeded';
    return 'unknown';
  }
  function nodeArtifacts(artifacts,key,taskId) {
    return artifacts.filter(a=>{
      if(a.task_id!==taskId) return false;
      const producer=a.node_id||a.producer||a.metadata?.source||'';
      const pathway=/pathway|pathabundance|pathcoverage/i.test(a.file_name||'');
      if(key==='report') return true;
      if(key==='functional'||key==='pathways') return ['functional_annotation','functional_plots'].includes(producer) && (key==='pathways'?pathway:!pathway);
      return ({data:['validate'],qc:['fastqc_raw','fastqc','fastp','reads_plots'],host:['host_depletion'],taxonomy:['taxonomy','taxonomy_plots']}[key]||[]).includes(producer);
    });
  }
  const statusLabels={completed:'已完成',succeeded:'已完成',running:'运行中',queued:'排队中',pending:'待运行',paused:'已暂停',cancelled:'已取消',failed:'失败',error:'失败',cached:'缓存复用',skipped:'已跳过',unknown:'未取得状态',validating:'校验中'};
  const model={templates,stages,nodeStatus,nodeArtifacts,statusLabels};
  if(typeof module==='object'&&module.exports)module.exports=model;
  else scope.BioFlowAnalysis=model;
})(typeof window==='object'?window:globalThis);
