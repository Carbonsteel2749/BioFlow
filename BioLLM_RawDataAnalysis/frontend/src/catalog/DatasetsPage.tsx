import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Database, FileText, Plus, RefreshCw, Search, ShieldCheck } from 'lucide-react'
import { catalogApi, type DatasetFile } from '../api/catalog'
import { tasksApi } from '../api/tasks'
import { DatasetDeleteAction } from './DatasetDeleteAction'
import { UploadPanel } from '../components/UploadPanel'
import { formatBytes } from '../artifact-center/catalog'
import { CatalogError, CatalogLoading, Pagination, taskStatus } from './CatalogShared'

const statusLabel=(value:string)=>({available:'可复用',needs_review:'待确认',unavailable:'不可用',changed:'清单已变化'}[value]??value)
const stageLabel=(value:string)=>({raw:'原始 reads',processed:'处理后 reads',unknown:'阶段待确认'}[value]??value)

function PendingUploads() {
  const [offset,setOffset]=useState(0)
  const query=useQuery({queryKey:['unpaired-uploads',offset],queryFn:()=>catalogApi.pending(offset)})
  return <details className="panel catalog-pending"><summary>未关联清单的上传文件{query.data?`（${query.data.total}）`:''}</summary><p>这些文件尚未建立可靠的样本配对关系，不会自动组合或启动分析。本期仅供核对，不提供删除操作。</p>
    {query.isLoading?<CatalogLoading/>:query.error?<CatalogError error={query.error} retry={()=>query.refetch()}/>:<><ul>{query.data?.items.map(file=><li key={file.id}><span>{file.original_name}</span><small>{formatBytes(file.size_bytes)} · 待确认</small></li>)}</ul>{query.data?.total===0&&<p>没有待核对的上传文件。</p>}<Pagination total={query.data?.total??0} offset={offset} onChange={setOffset}/></>}
  </details>
}

export function DatasetsPage() {
  const [input,setInput]=useState(''),[q,setQ]=useState(''),[offset,setOffset]=useState(0),[uploading,setUploading]=useState(false)
  const client=useQueryClient(),navigate=useNavigate()
  const query=useQuery({queryKey:['datasets',q,offset],queryFn:()=>catalogApi.datasets(q,offset)})
  const capabilities=useQuery({queryKey:['capabilities'],queryFn:tasksApi.capabilities,enabled:uploading})
  return <>
    <div className="page-heading"><div><div className="eyebrow"><Database size={15}/>数据与成果</div><h1>数据与样本</h1><p>上传一次，重复使用。按数据集管理样本、配对文件与关联任务。</p></div><button className="btn primary" onClick={()=>setUploading(v=>!v)}><Plus size={18}/>{uploading?'收起上传':'上传新数据'}</button></div>
    {uploading&&<section className="panel catalog-upload"><h2>上传数据集</h2><UploadPanel supported={capabilities.data?.file_uploads===true} onManifest={(_path,_count,id)=>{client.invalidateQueries({queryKey:['datasets']});client.invalidateQueries({queryKey:['unpaired-uploads']});if(id)navigate(`/datasets/${id}`);else setUploading(false)}}/></section>}
    <section className="panel catalog-panel"><form className="catalog-filters" onSubmit={event=>{event.preventDefault();setQ(input.trim());setOffset(0)}}><label className="search-box"><Search size={18}/><input aria-label="搜索数据集或样本" placeholder="搜索数据集名称或样本 ID" value={input} onChange={e=>setInput(e.target.value)} maxLength={200}/></label><button className="btn secondary" type="submit">搜索</button><button className="icon-btn" type="button" aria-label="刷新数据集" onClick={()=>query.refetch()}><RefreshCw size={18}/></button></form>
      {query.isLoading?<CatalogLoading/>:query.error?<CatalogError error={query.error} retry={()=>query.refetch()}/>:<>
        {query.data?.items.length?<div className="dataset-grid">{query.data.items.map(dataset=><article className="dataset-card" key={dataset.id}><div className="dataset-card-top"><div className="metric-icon teal"><Database size={24}/></div><span className={`catalog-badge ${dataset.status==='available'?'ready':'warning'}`}>{statusLabel(dataset.status)}</span></div><h2><Link to={`/datasets/${dataset.id}`}>{dataset.name}</Link></h2><span className="catalog-muted">{stageLabel(dataset.stage)} · {new Date(dataset.created_at).toLocaleDateString('zh-CN')}</span><div className="dataset-counts"><span><b>{dataset.sample_count}</b>个样本</span><span><b>{formatBytes(dataset.size_bytes)}</b>配对文件合计</span><span><b>{dataset.task_count}</b>关联任务</span></div>{dataset.reason&&<p className="catalog-warning">{dataset.reason}</p>}<div className="dataset-actions"><Link className="btn secondary" to={`/datasets/${dataset.id}`}>查看样本</Link>{dataset.status==='available'&&<Link className="btn primary" to={`/tasks/new?dataset=${dataset.id}`}>用于新分析</Link>}</div></article>)}</div>:<div className="catalog-state"><Database/><h2>{q?'没有匹配的数据集':'尚无数据集'}</h2><p>{q?'请尝试其他名称或样本 ID。':'上传配对 FASTQ 后，数据集会自动出现在这里。'}</p></div>}
        <Pagination total={query.data?.total??0} offset={offset} onChange={setOffset}/>
      </>}
    </section>
    <div className="catalog-note"><ShieldCheck size={18}/><span>样本身份由“数据集 + 样本 ID”共同确定。文件大小为逻辑大小，不代表独占磁盘空间；数据不会因重复使用而再次上传。</span></div>
    <PendingUploads/>
  </>
}

function ReadFile({file,mate}:{file:DatasetFile;mate:string}) {
  return <div className="dataset-read"><span className="catalog-badge">{mate}</span><div><b>{file.name}</b><span>{formatBytes(file.size_bytes)} · {file.availability==='available'?'文件可用':file.availability==='changed'?'文件已变化':'文件缺失或不可访问'}</span><small>{file.validation==='upload_initial_check'?'上传初检通过':'上传初检未记录'}</small>{file.checksum&&<details><summary>查看上传校验摘要</summary><code>{file.checksum}</code></details>}</div></div>
}

export function DatasetDetailPage() {
  const {id=''}=useParams()
  const [sampleOffset,setSampleOffset]=useState(0),[taskOffset,setTaskOffset]=useState(0)
  const [input,setInput]=useState(''),[sampleQ,setSampleQ]=useState('')
  const query=useQuery({queryKey:['dataset',id,sampleOffset,taskOffset,sampleQ],queryFn:()=>catalogApi.dataset(id,sampleOffset,taskOffset,sampleQ)})
  if(query.isLoading)return <CatalogLoading/>
  if(query.error)return <CatalogError error={query.error} retry={()=>query.refetch()}/>
  const data=query.data!
  return <>
    <Link className="back-link" to="/datasets"><ArrowLeft size={16}/>返回数据与样本</Link>
    <div className="page-heading"><div><div className="eyebrow"><Database size={15}/>{stageLabel(data.stage)}</div><h1>{data.name}</h1><p>{data.sample_count} 个样本 · {formatBytes(data.size_bytes)} · 数据集 ID：{data.id}</p></div>{data.status==='available'?<Link className="btn primary" to={`/tasks/new?dataset=${id}`}>使用此数据新建分析</Link>:<span className="catalog-badge warning">{statusLabel(data.status)}</span>}</div>
    <div className="dataset-actions"><DatasetDeleteAction id={id}/></div>
    <div className="catalog-note"><ShieldCheck size={18}/><span>{data.verification_note}</span></div>{data.reason&&<p className="catalog-warning" role="alert">{data.reason}</p>}
    <section className="panel catalog-panel" aria-label="样本列表"><div className="panel-header"><h2>样本与配对文件</h2><FileText size={20}/></div><form className="catalog-filters" onSubmit={event=>{event.preventDefault();setSampleQ(input.trim());setSampleOffset(0)}}><label className="search-box"><Search size={16}/><input aria-label="在数据集中搜索样本" value={input} onChange={e=>setInput(e.target.value)} maxLength={128}/></label><button className="btn secondary" type="submit">查找样本</button></form><div className="dataset-samples">{data.samples.map(sample=><article key={sample.sample_id}><h3>{sample.sample_id}</h3><ReadFile file={sample.read1} mate="R1"/><ReadFile file={sample.read2} mate="R2"/></article>)}{!data.samples.length&&<p>暂无匹配的样本配对信息。</p>}</div><Pagination total={data.sample_total??data.sample_count} offset={sampleOffset} onChange={setSampleOffset}/></section>
    <section className="panel catalog-panel" aria-label="关联任务列表"><div className="panel-header"><h2>关联分析任务</h2></div><div className="dataset-task-list">{data.tasks.map(task=><div key={`${task.kind}:${task.id}`}>{task.kind==='standard'?<Link to={`/tasks/${task.id}`} title={task.id}>{task.name||task.id}</Link>:<span>{task.id}（节点式流程）</span>}<span>{taskStatus(task.status)} · 流程校验：{task.validation_status==='succeeded'?'通过':task.validation_status==='failed'?'失败':'未通过或未记录'}</span></div>)}{!data.tasks.length&&<p>当前页没有关联任务。</p>}</div><Pagination total={data.task_count} offset={taskOffset} onChange={setTaskOffset}/></section>
    <section className="panel catalog-panel"><div className="panel-header"><h2>处理后数据来源</h2></div><div className="dataset-task-list">{data.processed_reads?.length?data.processed_reads.map(item=><div key={item.artifact_id}><span>{item.sample_id??'全局'} · {item.artifact_type==='reads.clean'?'清洗后 reads':'去宿主后 reads'} · {item.producer}</span><span>来源任务：{item.task_id}</span></div>):<p>尚无已登记的清洗或去宿主 reads 产物关系。</p>}<p className="catalog-muted">仅展示最近 100 条已登记关系，不提供处理后 reads 直接重跑标准流程的入口。</p></div></section>
  </>
}
