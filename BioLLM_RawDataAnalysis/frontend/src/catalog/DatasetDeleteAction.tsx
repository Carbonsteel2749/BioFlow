import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Trash2 } from 'lucide-react'
import { tasksApi } from '../api/tasks'
import { formatBytes } from '../artifact-center/catalog'
import '../components/task-cleanup.css'

export function DatasetDeleteAction({id}:{id:string}) {
  const [open,setOpen]=useState(false)
  return <><button type="button" className="btn danger-outline" onClick={()=>setOpen(true)}><Trash2 size={17}/>删除数据集</button>{open&&<DeleteDialog id={id} close={()=>setOpen(false)}/>}</>
}

function DeleteDialog({id,close}:{id:string;close:()=>void}) {
  const [confirmation,setConfirmation]=useState(''),[agreed,setAgreed]=useState(false)
  const ref=useRef<HTMLDialogElement>(null),client=useQueryClient(),navigate=useNavigate()
  const preview=useQuery({queryKey:['dataset-cleanup',id],queryFn:()=>tasksApi.datasetCleanupPreview(id),retry:false,gcTime:0,refetchOnWindowFocus:false})
  const mutation=useMutation({mutationFn:()=>tasksApi.datasetCleanup(id,preview.data!.token),onSuccess:()=>{
    client.removeQueries({queryKey:['dataset',id]})
    for(const key of ['datasets','unpaired-uploads','tasks','results'])void client.invalidateQueries({queryKey:[key]})
    close();navigate('/datasets')
  }})
  useEffect(()=>{ref.current?.showModal?.()},[])
  const ready=agreed&&confirmation===id&&preview.data?.dataset_cleanup?.will_delete&&!preview.isFetching&&!preview.error&&!mutation.isPending
  return <dialog ref={ref} open={typeof HTMLDialogElement.prototype.showModal!=='function'?true:undefined} className="cleanup-dialog" aria-label="删除数据集确认" onCancel={e=>{e.preventDefault();if(!mutation.isPending)close()}}>
    <h2>删除数据集</h2><p>删除数据集记录、样本清单及无引用的平台托管原始 FASTQ。不会自动删除关联任务；请先在任务中心处理它们。</p><p className="cleanup-task-id">数据集 ID：<code>{id}</code></p>
    {preview.isFetching?<p role="status">正在核实引用与文件…</p>:preview.error?<p role="alert">{preview.error.message}</p>:preview.data&&<section className="cleanup-preview"><b>{preview.data.dataset_cleanup?.will_delete?'可以清理此数据集':'数据集将保留'}</b>{preview.data.dataset_cleanup?.reasons.map(reason=><p key={reason}>{reason}</p>)}<strong>预计释放：约 {formatBytes(preview.data.estimated_bytes)}</strong><ul>{preview.data.scope.map(path=><li key={path}><code>{path}</code></li>)}</ul></section>}
    <button type="button" className="cleanup-recheck" disabled={preview.isFetching||mutation.isPending} onClick={()=>{setAgreed(false);void preview.refetch()}}>重新核实范围</button>
    <label className="cleanup-id-input">输入完整数据集 ID<input value={confirmation} onChange={e=>setConfirmation(e.target.value)} disabled={mutation.isPending}/></label>
    <label className="cleanup-confirm"><input type="checkbox" checked={agreed} onChange={e=>setAgreed(e.target.checked)} disabled={mutation.isPending}/>我已确认范围，了解原始文件删除后不可恢复</label>
    {mutation.error&&<p role="alert" className="cleanup-error">{mutation.error.message}</p>}
    <div className="cleanup-buttons"><button type="button" className="btn secondary" disabled={mutation.isPending} onClick={close}>返回</button><button type="button" className="btn danger-outline" disabled={!ready} onClick={()=>mutation.mutate()}>{mutation.isPending?'正在删除…':'确认删除数据集'}</button></div>
  </dialog>
}
