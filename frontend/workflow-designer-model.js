/* Portable design contract only. It is deliberately not an executable workflow payload. */
(function(root,factory){const model=factory();if(typeof module==='object'&&module.exports)module.exports=model;else root.BioFlowDesigner=model;})(typeof window!=='undefined'?window:globalThis,function(){
 'use strict';
 const clone=x=>JSON.parse(JSON.stringify(x));
 const roots=['fastq_input','clean_reads_input','host_removed_reads_input','contigs_input','mag_input','abundance_input'];
 const raw=['paired_raw_reads'],clean=['paired_clean_reads'],host=['paired_host_removed_reads'];
 const reads=[...raw,...clean,...host];
 const reports=['qc_report','fastp_metrics','host_depletion_metrics','taxonomy_abundance','taxonomy_report','functional_abundance','functional_report','pathway_tables','feature_table','amplicon_taxonomy','diversity_results','analysis_report','figure_collection','interpretation'];
 const input=(id,types,multiple=false,required=true,scope=['sample','cohort'])=>({id,accepted_data_types:types,accepted_scopes:scope,multiple,required});
 const output=(id,type,scope='sample')=>({id,data_type:type,scope});
 const param=(title,type,value,description,extra={})=>({title,type,default:value,description,...extra});
 const threads=()=>param('计算线程','integer',4,'此节点使用的并行线程数；不是服务器剩余空闲线程。',{minimum:1,maximum:64,unit:'线程'});
 const def=(label,tool,category,inputs,outputs,parameters={},description='',design=false)=>({label,tool,category,inputs,outputs,parameters,description,design});
 const definitions={
  start:def('开始','Workflow','基础',[],[output('control','control','cohort')],{},'流程入口；不读取或处理样本。',true),
  fastq_input:def('数据导入','Paired FASTQ','基础',[input('control',['control'],false,false)],[output('reads','paired_raw_reads')],{
   dataset_id:param('项目数据集','string','','选择当前项目已配对的数据集。此版本只保存选择，不上传文件。'),
   metadata_note:param('样本与分组备注','string','','记录分组、批次或配对设计；备注不替代已验证的样本信息表。')
  },'双端 FASTQ（R1 / R2）及样本信息。'),
  analysis_group:def('数据分析','分析容器','基础',[input('policy',['repair_policy'],false,false)],[],{},'框内节点处理数据；虚线连接控制修复策略，不传递 reads。',true),
  repair_llm:def('运行修复 LLM','故障诊断 / 续跑策略','AI',[],[output('policy','repair_policy','cohort')],{
   enabled:param('启用受限自动修复','boolean',false,'默认关闭；后端接线后，仅在失败时读取脱敏日志。'),
   model_id:param('模型连接','string','','独立选择运行修复使用的语言模型。'),
   max_retries:param('最大修复次数','integer',1,'超过次数或问题无法安全处理时暂停并通知用户。',{minimum:0,maximum:3,unit:'次'}),
   script_policy:param('脚本处理策略','string','task_copy','只使用本任务的副本，不改共享脚本。',{enum:['task_copy']}),
   resume_policy:param('恢复位置','string','failed_node','复用已完成节点的有效缓存，从失败计算节点重跑。',{enum:['failed_node']}),
   prompt:param('修复约束提示词','string','仅基于脱敏日志诊断。不得删除输入、扩大权限或更换科学方法。参数修改必须记录；脚本修改仅作用于任务副本。无法安全修复时暂停并告知用户。','此处只保存策略草稿，不实际调用模型。')
  },'失败时的辅助策略，不是必经的分析步骤。',true),
  fastqc:def('原始质量评估','FastQC','质控',[input('reads',reads,false,true,['sample'])],[output('report','qc_report')],{threads:threads()},'检查测序质量、接头及碱基组成；仅输出报告，不清洗 reads。'),
  fastp:def('质量控制与清洗','fastp','质控',[input('reads',raw,false,true,['sample'])],[output('reads','paired_clean_reads'),output('metrics','fastp_metrics')],{
   threads:threads(),qualified_quality_phred:param('合格碱基质量','integer',20,'低于此 Phred 分值的碱基计为低质量。',{minimum:0,maximum:93,unit:'Phred'}),
   length_required:param('最短保留长度','integer',50,'过滤清洗后过短的 reads。',{minimum:1,maximum:10000,unit:'bp'}),
   unqualified_percent_limit:param('低质量碱基上限','integer',40,'单条 read 中低质量碱基允许的最大百分比。',{minimum:0,maximum:100,unit:'%',advanced:true}),
   n_base_limit:param('N 碱基上限','integer',5,'单条 read 允许的 N 碱基数。',{minimum:0,maximum:10000,advanced:true}),
   cut_front:param('前端滑窗修剪','boolean',false,'从 read 前端进行质量滑窗修剪。',{advanced:true}),
   cut_tail:param('尾端滑窗修剪','boolean',false,'从 read 尾端进行质量滑窗修剪。',{advanced:true}),
   cut_window_size:param('滑窗大小','integer',4,'质量修剪时使用的窗口长度。',{minimum:1,maximum:1000,unit:'bp',advanced:true}),
   cut_mean_quality:param('滑窗平均质量阈值','integer',20,'滑窗平均质量低于阈值时修剪。',{minimum:0,maximum:93,advanced:true}),
   trim_poly_g:param('修剪 poly-G','boolean',false,'显式控制 poly-G 尾部修剪。',{advanced:true}),
   correction:param('双端重叠纠错','boolean',false,'对双端重叠区进行碱基纠错。',{advanced:true}),
   detect_adapter_for_pe:param('双端接头检测','boolean',true,'自动检测 paired-end 接头。',{advanced:true})
  },'去接头、低质量过滤与长度筛选。'),
  host_depletion:def('去除宿主序列','Bowtie2','分析',[input('reads',[...raw,...clean],false,true,['sample'])],[output('reads','paired_host_removed_reads'),output('metrics','host_depletion_metrics')],{
   threads:threads(),filter_mode:param('双端过滤策略','string','pair','按登记的双端过滤策略处理宿主匹配。',{enum:['pair','strict']}),
   bowtie2_preset:param('比对灵敏度','string','very-sensitive','影响比对灵敏度和运行时间。',{enum:['sensitive','very-sensitive','sensitive-local','very-sensitive-local']}),
   min_retained_pairs:param('最少保留 reads 对','integer',1,'低于此值应停止后续分析。',{minimum:0,unit:'对',advanced:true}),
   max_removed_pct:param('去除比例告警阈值','number',99,'宿主去除比例过高时要求核查样本与参考库。',{minimum:0,maximum:100,unit:'%',advanced:true})
  },'使用显式配置的宿主索引过滤 reads；数据库预检需后端接入。'),
  taxonomy:def('物种组成','Kraken2 / Bracken','分析',[input('reads',reads,false,true,['sample'])],[output('abundance','taxonomy_abundance'),output('report','taxonomy_report')],{
   threads:threads(),read_length:param('Bracken 读长','integer',100,'必须与数据库中已构建的 Bracken 读长匹配。',{minimum:1,maximum:10000,unit:'bp'})
  },'reads 分类与丰度估计；不据此判定某物种拥有某项总体功能。'),
  functional_annotation:def('基因与功能注释','HUMAnN','分析',[input('reads',reads,false,true,['sample'])],[output('functions','functional_abundance'),output('report','functional_report')],{threads:threads()},'基因家族、KO、EC 与通路结果；描述功能潜力，不等同于表达活性。'),
  pathways:def('代谢通路结果','HUMAnN 结果整理','结果',[input('functions',['functional_abundance'])],[output('pathways','pathway_tables')],{},'整理同次 HUMAnN 的通路丰度与覆盖度，不重复运行注释。',true),
  report:def('结果汇总','Tables / provenance','结果',[input('artifacts',reports,true)],[output('report','analysis_report','cohort')],{},'汇总各节点结果与来源，保留样本 ID、单位和分析限制。'),
  figures:def('科研图表','数值绘图 / Skill','结果',[input('results',['analysis_report','taxonomy_abundance','functional_abundance','diversity_results'],true)],[output('figures','figure_collection','cohort')],{
   recipe:param('图表方案','string','composition','图表由真实数值表渲染，不由 LLM 编造图中数值。',{enum:['composition','diversity','functional','overview']}),
   top_n:param('展示条目数','integer',20,'仅影响展示，不能用于截断 α / β 多样性计算矩阵。',{minimum:3,maximum:50}),
   dpi:param('位图导出分辨率','integer',300,'SVG 保留矢量；PNG 按此分辨率导出。',{minimum:150,maximum:600,unit:'dpi'})
  },'样本数与分组满足条件才可生成比较图；此轮不实际绘图。',true),
  interpret_llm:def('结果解读 LLM','基于证据的初步解读','AI',[input('results',['analysis_report'])],[output('text','interpretation','cohort')],{
   model_id:param('模型连接','string','','与运行修复模型独立配置。'),
   language:param('输出语言','string','中文','初步解读的输出语言。',{enum:['中文','English']}),
   prompt:param('解读提示词','string','仅依据已完成节点的结果表、单位与溯源进行描述。区分丰度、覆盖度和功能潜力；注明样本数、分组和局限。未经检验不报告显著性、因果或临床结论。每项判断引用来源。','不默认发送原始 FASTQ、完整日志或图片。')
  },'读取已验证结果表，输出有依据、有边界的初步结论。',true),
  end:def('输出','结果 / 图表 / 解读','基础',[input('artifacts',['analysis_report','figure_collection','interpretation'],true)],[],{},'最终交付入口；本版只预览输出结构，不生成压缩包。',true),
  amplicon_trim:def('扩增子引物处理','cutadapt · 设计','16S',[input('reads',raw)],[output('reads','amplicon_reads')],{
   forward_primer:param('正向引物','string','','按实际扩增区域填写；不得使用未知引物。'),reverse_primer:param('反向引物','string','','按实际实验填写。'),threads:threads()
  },'16S 扩增子流程；工具与数据契约待后端适配。',true),
  dada2:def('去噪与 ASV 构建','DADA2 · 设计','16S',[input('reads',['amplicon_reads'])],[output('table','feature_table','cohort'),output('sequences','representative_sequences','cohort')],{
   trunc_len_f:param('R1 截断长度','integer',0,'结合质量曲线和双端重叠选择；0 表示暂未指定截断。',{minimum:0,maximum:10000,unit:'bp'}),
   trunc_len_r:param('R2 截断长度','integer',0,'需保留足够双端重叠，不能照抄其他项目参数。',{minimum:0,maximum:10000,unit:'bp'}),threads:threads()
  },'后端待接入：构建 ASV 特征表和代表序列；不是宏基因组功能注释。',true),
  amplicon_taxonomy:def('ASV 分类','16S 参考分类器 · 设计','16S',[input('sequences',['representative_sequences'])],[output('taxonomy','amplicon_taxonomy','cohort')],{
   reference_profile:param('参考库 / 扩增区域','string','','后续选择与扩增区域匹配的参考分类器及版本。')
  },'参考数据库、分类器与扩增区域需要共同校验。',true),
  diversity:def('多样性分析','α / β diversity · 设计','16S',[input('table',['feature_table'])],[output('diversity','diversity_results','cohort')],{
   depth:param('抽平深度（待确认）','integer',0,'根据样本深度分布确定；0 表示尚未决定，不代表直接运行。',{minimum:0,unit:'reads'}),
   grouping:param('分组字段','string','','来自样本 metadata 的字段；不填时仅做描述。')
  },'保留多样性方法和样本过滤依据，不能从单样本生成组间检验。',true)
 };
 function catalog(registry){
  const c=clone(definitions);
  const labels={assembly:'联合组装',binning:'基因组分箱',bin_refinement:'MAG 质量筛选',bin_quantification:'样本—MAG 定量',bin_reassembly:'候选 Bin 重组装',bin_annotation:'MAG 分类与功能注释'};
  const descriptions={assembly:'将清洗或去宿主后的 reads 组装为 contigs；联合组装输出属于队列，不属于单一样本。',binning:'根据已登记的分箱方法，从组装结果中生成候选基因组 bins。',bin_refinement:'按完整度和污染度筛选候选 bins；筛选阈值不等同于完整质量认证。',bin_quantification:'将各样本 reads 回贴到 MAG，生成 sample_id + mag_id 的定量证据，不将联合组装 MAG 归属于单一样本。',bin_reassembly:'对候选 bin 进行可选重组装；保留原分箱与重组装结果的对应关系。',bin_annotation:'对 MAG 进行分类和功能注释；需要独立的分类与功能数据库，不能用群落总体功能替代 MAG 注释。'};
  for(const d of registry?.nodes||[]){
   if(!d||typeof d.type!=='string'||!/^[A-Za-z][A-Za-z0-9_]*$/.test(d.type)||['constructor','prototype','__proto__'].includes(d.type))continue;
   if(!Object.hasOwn(c,d.type))c[d.type]=def(labels[d.type]||d.label||d.type,(d.tool_requirements||[]).join(' · ')||d.method_id||d.type,({mag:'MAG',qc:'质控',preprocessing:'质控',report:'结果'})[d.category]||'分析',[],[],{},d.description||descriptions[d.type]||'分析服务登记的方法；参数、输入输出与执行映射由同一注册表提供。');
   const node=c[d.type];node.registered=true;
   node.requirements={tools:clone(d.tool_requirements||[]),databases:clone(d.database_requirements||[])};
   if(Array.isArray(d.inputs))node.inputs=clone(d.inputs);
   if(Array.isArray(d.outputs))node.outputs=clone(d.outputs);
   const props=d.parameters_schema?.properties||d.parameters_schema||{};
   for(const [key,value] of Object.entries(props))if(value&&typeof value==='object')node.parameters[key]={...node.parameters[key],...clone(value),title:node.parameters[key]?.title||value.title||key,description:node.parameters[key]?.description||value.description||'服务登记参数；请核对该工具的含义。'};
   // The start link is a frontend control edge, not an execution-engine input.
   if(roots.includes(d.type)){node.inputs=[input('control',['control'],false,false)];node.category='基础';node.inputRoot=true;}
   if(d.type==='report'){const artifacts=node.inputs.find(p=>p.id==='artifacts');if(artifacts)artifacts.accepted_data_types=[...new Set([...artifacts.accepted_data_types,...reports])];}
  }
  return c;
 }
 const defaultCatalog=catalog();
 const uid=(prefix,g)=>{let i=1;while(g.nodes?.some(n=>n.id===prefix+'_'+i)||g.edges?.some(e=>e.id===prefix+'_'+i))i++;return prefix+'_'+i;};
 function addNode(g,type,c=defaultCatalog){
  const d=c[type];if(!d)throw Error('未知节点类型');if(g.nodes.length>=100)throw Error('最多支持 100 个节点');
  if(['start','analysis_group','end','repair_llm'].includes(type)&&g.nodes.some(n=>n.type===type))throw Error('此基础节点只能添加一次');
  const n={id:uid(type,g),type,label:d.label,x:340,y:130,group:['质控','分析','MAG','16S'].includes(d.category)||type==='repair_llm'?'analysis':null,parameters:Object.fromEntries(Object.entries(d.parameters).map(([k,s])=>[k,clone(s.default??'')])),custom:[]};
  g.nodes.push(n);return n;
 }
 function checkConnection(g,source,sourcePort,target,targetPort,c=defaultCatalog,ignoreEdge=null){
  const fail=message=>({ok:false,message});const a=g.nodes.find(n=>n.id===source),b=g.nodes.find(n=>n.id===target);
  if(!a||!b||a===b)return fail('不能连接到自身或不存在的节点');
  const out=c[a.type]?.outputs.find(p=>p.id===sourcePort),into=c[b.type]?.inputs.find(p=>p.id===targetPort);
  if(!out||!into)return fail('输入或输出端口不存在');
  if(!into.accepted_data_types.includes(out.data_type)||!into.accepted_scopes.includes(out.scope))return fail('数据类型或样本范围不兼容，不能连接');
  const edges=g.edges.filter(e=>e.id!==ignoreEdge);
  if(edges.some(e=>e.source===source&&e.sourcePort===sourcePort&&e.target===target&&e.targetPort===targetPort))return fail('已存在此连接');
  if(!into.multiple&&edges.some(e=>e.target===target&&e.targetPort===targetPort))return fail('该输入只接受一条连接，请先移除旧连接');
  const seen=new Set();function reaches(n){if(n===source)return true;if(seen.has(n))return false;seen.add(n);return edges.filter(e=>e.source===n).some(e=>reaches(e.target));}
  if(reaches(target))return fail('此连接会产生循环；第一版不支持循环执行');
  const warning=['taxonomy','functional_annotation'].includes(b.type)&&out.data_type!=='paired_host_removed_reads'?{code:'raw_annotation',message:'注释输入未经过宿主过滤。确认保留此分析风险连接？'}:b.type==='host_depletion'&&out.data_type==='paired_raw_reads'?{code:'host_without_qc',message:'直接对原始 reads 去宿主，未先经 fastp 清洗。确认保留此风险连接？'}:null;
  return {ok:true,warning,kind:out.data_type==='repair_policy'?'policy':out.data_type==='control'?'control':'data'};
 }
 function connect(g,source,sourcePort,target,targetPort,confirmed=false,c=defaultCatalog){
  const result=checkConnection(g,source,sourcePort,target,targetPort,c);if(!result.ok)throw Error(result.message);
  if(result.warning&&!confirmed)throw Error('需二次确认：'+result.warning.message);
  if(g.edges.length>=300)throw Error('最多支持 300 条连接');
  const edge={id:uid('edge',g),source,sourcePort,target,targetPort,kind:result.kind,acknowledged:!!(result.warning&&confirmed)};g.edges.push(edge);return edge;
 }
 const templates=[
  {id:'metagenomics',name:'标准宏基因组',description:'质量评估、清洗、去宿主，物种与功能并行分析。',tag:'现有分析方法',icon:'dna'},
  {id:'qc',name:'仅质控与清洗',description:'检查原始 reads 质量，清洗并汇总质控报告。',tag:'轻量流程',icon:'qc'},
  {id:'taxonomy',name:'物种组成分析',description:'清洗与去宿主后，进行 reads 层面的组成分析。',tag:'Kraken2 / Bracken',icon:'taxonomy'},
  {id:'functional',name:'功能潜力分析',description:'基因家族、KO、EC 和通路；不替代转录组分析。',tag:'HUMAnN',icon:'function'},
  {id:'amplicon16s',name:'16S 扩增子分析',description:'引物处理、ASV、分类与多样性。后端尚未接入。',tag:'设计模板 · 待接入',icon:'dna'},
  {id:'blank',name:'从空白开始',description:'保留起点、输入、分析容器与输出，自行添加节点。',tag:'自定义',icon:'plus'}
 ];
 function template(kind,c=defaultCatalog){
  const meta=templates.find(t=>t.id===kind);if(!meta)throw Error('模板不存在');
  const g={format:'bioflow.workflow-design',version:1,execution:'design_only',name:meta.name,nodes:[],edges:[]};
  const make=t=>addNode(g,t,c),link=(a,ap,b,bp)=>connect(g,a.id,ap,b.id,bp,true,c);
  const start=make('start'),data=make('fastq_input'),group=make('analysis_group'),repair=make('repair_llm'),end=make('end');
  link(start,'control',data,'control');link(repair,'policy',group,'policy');
  if(kind==='blank'){arrange(g,c);return g;}
  const report=make('report'),fig=make('figures'),interpret=make('interpret_llm');
  link(report,'report',fig,'results');link(report,'report',interpret,'results');link(report,'report',end,'artifacts');link(fig,'figures',end,'artifacts');link(interpret,'text',end,'artifacts');
  if(kind==='amplicon16s'){
   const trim=make('amplicon_trim'),asv=make('dada2'),tax=make('amplicon_taxonomy'),div=make('diversity');
   link(data,'reads',trim,'reads');link(trim,'reads',asv,'reads');link(asv,'sequences',tax,'sequences');link(asv,'table',div,'table');link(tax,'taxonomy',report,'artifacts');link(div,'diversity',report,'artifacts');fig.parameters.recipe='diversity';
  }else{
   const qc=make('fastqc'),p=make('fastp');link(data,'reads',qc,'reads');link(data,'reads',p,'reads');link(qc,'report',report,'artifacts');link(p,'metrics',report,'artifacts');
   if(kind!=='qc'){
    const h=make('host_depletion');link(p,'reads',h,'reads');link(h,'metrics',report,'artifacts');
    if(kind!=='functional'){const t=make('taxonomy');link(h,'reads',t,'reads');link(t,'abundance',report,'artifacts');}
    if(kind!=='taxonomy'){const f=make('functional_annotation'),p=make('pathways');p.group='analysis';link(h,'reads',f,'reads');link(f,'functions',p,'functions');link(f,'functions',report,'artifacts');link(p,'pathways',report,'artifacts');}
   }
   if(kind==='functional')fig.parameters.recipe='functional';if(kind==='qc')fig.parameters.recipe='overview';
  }
  arrange(g,c);return g;
 }
 function arrange(g,c=defaultCatalog){
  const inner=g.nodes.filter(n=>n.group==='analysis'&&n.type!=='repair_llm'&&!roots.includes(n.type));
  const levels=new Map();const pending=new Set(inner.map(n=>n.id));let guard=0;
  while(pending.size&&guard++<100){for(const n of inner){if(!pending.has(n.id))continue;const deps=g.edges.filter(e=>e.target===n.id&&pending.has(e.source));if(deps.length)continue;const parents=g.edges.filter(e=>e.target===n.id).map(e=>levels.get(e.source)??-1);levels.set(n.id,Math.max(-1,...parents)+1);pending.delete(n.id);}}
  const rows={};for(const n of inner){const level=levels.get(n.id)||0;const row=rows[level]||0;rows[level]=row+1;n.x=350+level*260;n.y=300+row*210;}
  const right=Math.max(900,...inner.map(n=>n.x+250));
  let rootRow=0;for(const n of g.nodes){if(n.type==='start'){n.x=30;n.y=70;}if(roots.includes(n.type)){n.x=30;n.y=300+rootRow++*210;}if(n.type==='analysis_group'){n.x=310;n.y=50;}if(n.type==='repair_llm'){n.x=350;n.y=110;}
   if(n.type==='report'){n.x=right+50;n.y=300;}if(n.type==='figures'){n.x=right+320;n.y=160;}if(n.type==='interpret_llm'){n.x=right+320;n.y=420;}if(n.type==='end'){n.x=right+600;n.y=300;}
   if(!n.group&&!roots.includes(n.type)&&!['start','analysis_group','report','figures','interpret_llm','end'].includes(n.type)){n.x=right+50;n.y=660;}
  }
  return g;
 }
 function groupBounds(g){const members=g.nodes.filter(n=>n.group==='analysis');return {x:310,y:50,width:Math.max(610,...members.map(n=>n.x+250-310)),height:Math.max(560,...members.map(n=>n.y+190-50))};}
 function parameterError(s,v){
  if(s.type==='boolean')return typeof v!=='boolean';
  if(['number','integer'].includes(s.type))return typeof v!=='number'||!Number.isFinite(v)||(s.type==='integer'&&!Number.isInteger(v))||(s.minimum!==undefined&&v<s.minimum)||(s.maximum!==undefined&&v>s.maximum);
  return typeof v!=='string'||v.length>12000||(s.enum&&!s.enum.includes(v));
 }
 function validate(g,c=defaultCatalog){
  const errors=[],warnings=[];const add=(arr,code,message,node)=>arr.push({code,message,node});
  for(const type of ['start','analysis_group','end'])if(!g.nodes.some(n=>n.type===type))add(errors,'missing_node','缺少 '+c[type].label);
  if(!g.nodes.some(n=>roots.includes(n.type)&&(n.type==='fastq_input'||c[n.type]?.registered)))add(errors,'missing_node','缺少已登记输入根节点');
  for(const n of g.nodes){
   const d=c[n.type];if(!d){add(errors,'unknown_node','未知节点类型',n.id);continue;}
   // End is a frontend delivery view. Native-output workflows deliberately
   // omit legacy REPORT; they need no invented execution edge into this view.
   const nativeOutputView=n.type==='end'&&!g.nodes.some(node=>node.type==='report');
   for(const p of d.inputs)if(p.required&&!nativeOutputView&&!g.edges.some(e=>e.target===n.id&&e.targetPort===p.id))add(errors,'missing_input',n.label+'：尚未连接 '+p.id+' 输入',n.id);
   for(const [k,s] of Object.entries(d.parameters))if(parameterError(s,n.parameters[k]??s.default))add(errors,'invalid_parameter',n.label+'：'+s.title+' 的类型或范围不正确',n.id);
   const keys=new Set(Object.keys(d.parameters));
   for(const p of n.custom||[]){if(!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(p.name)||keys.has(p.name))add(errors,'custom_key','自定义参数名非法或重复：'+p.name,n.id);keys.add(p.name);if(!['string','number','integer','boolean'].includes(p.type)||parameterError({type:p.type},p.value))add(errors,'custom_type','自定义参数值类型不匹配：'+p.name,n.id);}
   if(n.custom?.length)add(warnings,'custom_unmapped',n.label+'：自定义参数尚未映射后端命令，不会执行',n.id);
   if(d.design&&['16S'].includes(d.category))add(warnings,'design_only',n.label+'：仅设计，工具和数据契约待接入',n.id);
   if(n.type==='fastq_input'&&!n.parameters.dataset_id)add(warnings,'dataset','尚未绑定当前项目数据集',n.id);
   if(['repair_llm','interpret_llm'].includes(n.type)&&n.parameters.enabled!==false&&!n.parameters.model_id)add(warnings,'model','尚未为 '+n.label+' 选择模型连接',n.id);
  }
  for(const e of g.edges){const r=checkConnection(g,e.source,e.sourcePort,e.target,e.targetPort,c,e.id);if(!r.ok)add(errors,'invalid_edge',r.message,e.target);else if(r.warning)add(warnings,r.warning.code,r.warning.message,e.target);}
  return {errors,warnings};
 }
 function importJSON(text,{portable=true,c=defaultCatalog}={}){
  if(typeof text!=='string'||text.length>2000000)throw Error('模板大小超过 2 MB 限制');
  let g;try{g=JSON.parse(text,(k,v)=>{if(['__proto__','prototype','constructor'].includes(k))throw Error('非法键名');return v;});}catch(e){throw Error('JSON 格式无效或含非法键名');}
  if(g?.format!=='bioflow.workflow-design'||g.version!==1||!Array.isArray(g.nodes)||!Array.isArray(g.edges)||g.nodes.length>100||g.edges.length>300)throw Error('不是支持的 BioFlow Workflow v1 设计模板');
  const ids=new Set(),types=new Set();const nodes=g.nodes.map(n=>{
   if(!n||!c[n.type]||typeof n.id!=='string'||!/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(n.id))throw Error('未知节点类型或非法编号');
   if(ids.has(n.id))throw Error('节点编号重复');ids.add(n.id);
   if(['start','analysis_group','end','repair_llm'].includes(n.type)&&types.has(n.type))throw Error('基础节点重复');types.add(n.type);
   if(!Number.isFinite(n.x)||!Number.isFinite(n.y)||n.x<0||n.y<0||n.x>30000||n.y>30000)throw Error('节点坐标无效');
   if(!n.parameters||typeof n.parameters!=='object'||Array.isArray(n.parameters))throw Error('节点参数无效');
   const params={};for(const [k,v] of Object.entries(n.parameters)){if(portable&&roots.includes(n.type)&&k==='dataset_id')continue;if(!Object.hasOwn(c[n.type].parameters,k))throw Error('未知参数或凭据：'+k);if(typeof v==='object'||String(v).length>12000)throw Error('参数仅接受有限标量');params[k]=v;}
   for(const [k,s] of Object.entries(c[n.type].parameters))if(!Object.hasOwn(params,k))params[k]=clone(s.default??'');
   if(portable&&roots.includes(n.type)&&Object.hasOwn(params,'dataset_id'))params.dataset_id='';
   if(portable&&['repair_llm','interpret_llm'].includes(n.type))params.model_id='';
   const custom=n.custom||[];if(!Array.isArray(custom)||custom.length>40)throw Error('自定义参数过多');
   const safe=custom.map(p=>{if(!p||typeof p.name!=='string'||/key|password|secret|token|authorization/i.test(p.name)||typeof p.value==='object'||String(p.value).length>12000)throw Error('自定义参数不允许凭据或复杂对象');return {name:p.name.slice(0,64),type:p.type,value:p.value,description:String(p.description||'').slice(0,600)};});
   return {id:n.id,type:n.type,label:String(n.label||c[n.type].label).slice(0,80),x:n.x,y:n.y,group:n.group==='analysis'?'analysis':null,parameters:params,custom:safe};
  });
  const edgeIds=new Set();const edges=g.edges.map(e=>{if(!e||!ids.has(e.source)||!ids.has(e.target)||typeof e.id!=='string'||!/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(e.id)||edgeIds.has(e.id))throw Error('连接引用错误或编号重复');edgeIds.add(e.id);if(!c[nodes.find(n=>n.id===e.source).type].outputs.some(p=>p.id===e.sourcePort)||!c[nodes.find(n=>n.id===e.target).type].inputs.some(p=>p.id===e.targetPort))throw Error('连接端口不存在');return {id:e.id,source:e.source,sourcePort:e.sourcePort,target:e.target,targetPort:e.targetPort,kind:['policy','control'].includes(e.kind)?e.kind:'data',acknowledged:e.acknowledged===true};});
  const result={format:'bioflow.workflow-design',version:1,execution:'design_only',name:String(g.name||'导入流程').slice(0,120),nodes,edges};
  for(const e of edges){const check=checkConnection(result,e.source,e.sourcePort,e.target,e.targetPort,c,e.id);if(!check.ok)throw Error(check.message);if(check.warning&&!e.acknowledged)throw Error('风险连接尚未确认，请在画布中重新连接并确认');e.kind=check.kind;}
  return result;
 }
 function exportJSON(g,{portable=true}={}){for(const n of g.nodes){if(Object.keys(n.parameters).some(k=>/api.?key|password|secret|access.?token|authorization/i.test(k))||(n.custom||[]).some(p=>/key|password|secret|token|authorization/i.test(p.name)))throw Error('不允许保存凭据参数，请移除密钥或密码字段');if(Object.values(n.parameters).concat((n.custom||[]).map(p=>p.value)).some(v=>typeof v==='number'&&!Number.isFinite(v)))throw Error('参数包含非有限数值，无法保存');if(n.custom?.length>40)throw Error('每个节点最多支持 40 个自定义参数');}const copy=clone(g);copy.execution='design_only';if(portable){delete copy.input_bindings;for(const n of copy.nodes){if(roots.includes(n.type)){delete n.dataset_id;delete n.input_bindings;if(Object.hasOwn(n.parameters,'dataset_id'))n.parameters.dataset_id='';}if(['repair_llm','interpret_llm'].includes(n.type))n.parameters.model_id='';}}return JSON.stringify(copy,null,2);}
 function parameterValue(type,raw){if(type==='boolean')return raw===true||raw==='true';if(['number','integer'].includes(type)&&String(raw).trim()!==''&&Number.isFinite(Number(raw)))return Number(raw);return String(raw);}
 function addCustom(n){if(n.custom.length>=40)throw Error('每个节点最多支持 40 个自定义参数');let i=1;while(n.custom.some(p=>p.name==='custom_'+i))i++;const p={name:'custom_'+i,type:'string',value:'',description:''};n.custom.push(p);return p;}
 function removeNode(g,nid){g.nodes=g.nodes.filter(n=>n.id!==nid);g.edges=g.edges.filter(e=>e.source!==nid&&e.target!==nid);}
 const draftKey=(user,project)=>'bioflow.workflow-design.v1.'+JSON.stringify([String(user),String(project)]);
 function fromWorkflow(record,c=defaultCatalog){const g=template('blank',c);removeNode(g,g.nodes.find(n=>n.type==='fastq_input').id);const map=new Map();for(const item of record.workflow.nodes){const n=addNode(g,item.type,c);n.parameters={...n.parameters,...clone(item.parameters||{})};map.set(item.id,n.id);if(!roots.includes(n.type)&&n.type!=='report')n.group='analysis';}for(const e of record.workflow.edges)connect(g,map.get(e.source_node),e.source_port,map.get(e.target_node),e.target_port,true,c);const start=g.nodes.find(n=>n.type==='start');for(const n of g.nodes.filter(n=>roots.includes(n.type)))connect(g,start.id,'control',n.id,'control',true,c);const report=g.nodes.find(n=>n.type==='report');if(report)connect(g,report.id,'report',g.nodes.find(n=>n.type==='end').id,'artifacts',true,c);g.name=record.name;arrange(g,c);return g;}
 return {roots,fromWorkflow,catalog,templates,template,addNode,connect,checkConnection,validate,arrange,groupBounds,importJSON,exportJSON,removeNode,draftKey,parameterValue,addCustom};
});
