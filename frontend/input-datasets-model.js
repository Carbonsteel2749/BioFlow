(function(root,factory){const m=factory();if(typeof module==='object'&&module.exports)module.exports=m;else root.BioFlowInputs=m;})(typeof window==='object'?window:globalThis,function(){
 'use strict';
 const roots=['fastq_input','clean_reads_input','host_removed_reads_input','contigs_input','mag_input','abundance_input'];
 const draft=()=>({kind:'reads',processing_status:'raw',processing_note:'',samples:[{sample_id:'',read1:'',read2:''}],mags:[{mag_id:'',fasta:''}],cohort_id:'',fasta:'',table:'',feature_type:'species',unit:'',normalization:''});
 function spec(d,confirmed=false){
  const need=(v,label)=>{if(typeof v!=='string'||!v.trim())throw Error('请填写或选择'+label);return v.trim();};
  const s={kind:d.kind};
  if(d.kind==='reads'||d.kind==='mags'){
   const reads=d.kind==='reads',rows=d[reads?'samples':'mags'],key=reads?'sample_id':'mag_id',ids=new Set();
   if(!rows.length||rows.length>(reads?1024:10000))throw Error('输入行数超出上限');
   s[reads?'samples':'mags']=rows.map(r=>{const name=need(r[key],reads?'样本 ID':'MAG ID');if(ids.has(name))throw Error('ID 必须唯一');ids.add(name);return reads?{sample_id:name,read1:need(r.read1,'R1'),read2:need(r.read2,'R2')}:{mag_id:name,fasta:need(r.fasta,'FASTA')};});
   if(reads){s.processing_status=d.processing_status;if(!['raw','clean','host_removed'].includes(s.processing_status))throw Error('处理状态无效');if(s.processing_status!=='raw'){s.processing_note=need(d.processing_note,'处理说明');if(confirmed)s.confirmed=true;}}
  }
  if(['mags','contigs'].includes(d.kind))s.cohort_id=need(d.cohort_id,'队列 ID');
  if(d.kind==='contigs')s.fasta=need(d.fasta,'FASTA');
  if(d.kind==='matrix'){s.table=need(d.table,'矩阵文件');s.feature_type=need(d.feature_type,'特征类型');s.unit=need(d.unit,'单位');s.normalization=need(d.normalization,'标准化说明');}
  return s;
 }
 function refs(s){return s.kind==='reads'?s.samples.flatMap(r=>[r.read1,r.read2]):s.kind==='mags'?s.mags.map(r=>r.fasta):[s.kind==='contigs'?s.fasta:s.table];}
 const selectedBytes=(s,candidates)=>{const selected=new Set(refs(s));return candidates.reduce((sum,c)=>sum+(selected.has(c.id)?Number(c.size_bytes)||0:0),0);};
 function bindings(g,registry,values,records){
  if(g.nodes.some(n=>n.type==='report'))throw Error('类型化输入不支持旧 CSV REPORT；请选择不含 REPORT 的模板，保留原设计并修正后发布');
  const result={};for(const n of g.nodes.filter(n=>roots.includes(n.type))){const type=registry.find(d=>d.type===n.type)?.outputs?.[0]?.data_type,record=records.find(r=>r.dataset_id===values[n.id]);if(!record)throw Error('缺少输入：'+n.id);if(!type||record.contract?.data_type!==type)throw Error('输入不兼容：'+n.id);result[n.id]=record.dataset_id;}if(!Object.keys(result).length)throw Error('缺少已登记输入根节点');return result;
 }
 function evidence(record){if(Array.isArray(record))return record.map(evidence);if(record&&typeof record==='object')return Object.fromEntries(Object.entries(record).filter(([k])=>!['path','root','source_root','source_file','staged_file','output_file','read1','read2','fasta','table'].includes(k)).map(([k,v])=>[k,evidence(v)]));return record;}
 return {roots,draft,spec,refs,selectedBytes,bindings,evidence};
});
