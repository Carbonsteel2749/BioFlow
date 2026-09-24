import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { BarChart3, RefreshCw, ShieldCheck } from 'lucide-react'
import { catalogApi, type CatalogResult } from '../api/catalog'
import { tasksApi } from '../api/tasks'
import { ArtifactCard, ArtifactDetail } from '../artifact-center/ArtifactCenter'
import { CatalogError, CatalogLoading, Pagination, taskStatus } from './CatalogShared'

export function ResultsPage() {
  const [form,setForm]=useState({task_id:'',sample_id:'',artifact_type:''})
  const [filters,setFilters]=useState(form),[offset,setOffset]=useState(0),[selected,setSelected]=useState<CatalogResult|null>(null)
  const [indexOffset,setIndexOffset]=useState(0),[revision,setRevision]=useState(0)
  const index=useQuery({queryKey:['result-index',indexOffset,revision],queryFn:()=>catalogApi.refreshResults(indexOffset),retry:false,staleTime:30000,refetchOnWindowFocus:false})
  const query=useQuery({queryKey:['results',filters,offset,index.dataUpdatedAt],queryFn:()=>catalogApi.results({...filters,offset}),enabled:!index.isPending})
  const types=useQuery({queryKey:['result-types'],queryFn:catalogApi.resultTypes})
  return <>
    <div className="page-heading"><div><div className="eyebrow"><BarChart3 size={15}/>数据与成果</div><h1>结果中心</h1><p>跨任务查找已登记的图表、结果表和报告，保留每份成果的来源。</p></div><button className="btn secondary" disabled={index.isFetching} onClick={()=>{setIndexOffset(0);setRevision(v=>v+1)}}><RefreshCw size={18} className={index.isFetching?'spin':''}/>{index.isFetching?'正在登记最新产物…':'刷新结果'}</button></div>
    {index.error&&<p className="catalog-warning">最新产物刷新失败：{index.error.message}。仍可查看已登记结果。</p>}
    {Boolean(index.data?.remaining)&&<div className="catalog-note"><span>最新一批任务已刷新，还有 {index.data?.remaining} 个较早任务可检查。</span><button className="btn secondary" disabled={index.isFetching} onClick={()=>setIndexOffset(index.data?.next_offset??0)}>继续刷新较早任务</button></div>}
    {Boolean(index.data?.warnings?.length)&&<p className="catalog-warning">部分任务的阶段产物未能登记，请到任务详情核对。已登记结果仍可使用。</p>}
    <div className="catalog-note"><ShieldCheck size={18}/><span>同名样本不代表同一个样本；不同任务的数据库和参数可能不同。本页只检索，不合并丰度或进行统计比较。文件下载时执行完整性校验。</span></div>
    <section className="panel catalog-panel"><form className="catalog-filters result-filters" onSubmit={event=>{event.preventDefault();setFilters({task_id:form.task_id.trim(),sample_id:form.sample_id.trim(),artifact_type:form.artifact_type});setOffset(0)}}>
      <label>来源任务 ID<input value={form.task_id} placeholder="完整任务 ID，可留空" onChange={e=>setForm(v=>({...v,task_id:e.target.value}))} maxLength={128}/></label>
      <label>样本 ID<input value={form.sample_id} placeholder="样本 ID，可留空" onChange={e=>setForm(v=>({...v,sample_id:e.target.value}))} maxLength={128}/></label>
      <label>结果类型<select value={form.artifact_type} onChange={e=>setForm(v=>({...v,artifact_type:e.target.value}))}><option value="">全部类型</option>{Array.isArray(types.data)&&types.data.map(type=><option key={type}>{type}</option>)}</select></label><button className="btn primary" type="submit">筛选</button><button className="btn secondary" type="button" onClick={()=>{setForm({task_id:'',sample_id:'',artifact_type:''});setFilters({task_id:'',sample_id:'',artifact_type:''});setOffset(0)}}>重置</button>
    </form>{types.error&&<p className="catalog-warning">结果类型列表暂不可用，仍可按任务和样本筛选。</p>}
    {index.isPending||query.isLoading?<CatalogLoading/>:query.error?<CatalogError error={query.error} retry={()=>query.refetch()}/>:<>
      {query.data?.items.length?<div className="global-results">{query.data.items.map(item=><article className="global-result" key={item.artifact_id}><div className="global-result-source"><div>{item.task_kind==='standard'?<Link aria-label={`来源任务 ${item.task_id}`} to={`/tasks/${item.task_id}`} title={item.task_id}>来源任务 {item.task_name||item.task_id}</Link>:<span>节点式任务 {item.task_id}</span>}<span>{taskStatus(item.task_status)} · {new Date(item.created_at).toLocaleString('zh-CN')}</span></div><span className={`catalog-badge ${item.partial?'warning':'ready'}`}>{item.partial?'阶段性结果':'任务已完成'}</span></div>
        <ArtifactCard artifact={item} onOpen={()=>setSelected(item)}/>
        <div className="global-result-links"><span>数据库 profile：{typeof item.metadata.database_profile==='string'?item.metadata.database_profile:'未记录'} · release：{typeof item.metadata.database_release==='string'?item.metadata.database_release:'未记录'}</span>{item.task_kind==='standard'&&<Link to={`/tasks/${item.task_id}#result-insights`}>查看初步解读</Link>}{item.has_result_archive&&<a href={tasksApi.resultUrl(item.task_id)} download>下载任务结果包</a>}</div>
      </article>)}</div>:<div className="catalog-state"><BarChart3/><h2>没有匹配的结果</h2><p>可调整筛选条件，或到任务详情检查产物是否已经生成并登记。</p><Link to="/tasks" className="btn secondary">前往任务中心</Link></div>}
      <Pagination total={query.data?.total??0} offset={offset} onChange={setOffset}/>
    </>}
    </section>
    <p className="catalog-muted">节点式流程仅展示已登记到共享产物库、具备明确类型的结果；未经类型映射的内部文件不会按文件名猜测用途。</p>
    {selected&&<ArtifactDetail artifact={{...selected,metadata:{...selected.metadata,parameters:selected.metadata.parameters??selected.run_parameters}}} onClose={()=>setSelected(null)}/>}
  </>
}
