import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Pencil } from 'lucide-react'
import { tasksApi } from '../api/tasks'
import type { AnalysisTask } from '../types'
import './task-cleanup.css'

export function TaskRenameAction({task}:{task:AnalysisTask}) {
  const [open,setOpen]=useState(false)
  return <><button type="button" className="icon-btn" title="重命名任务" aria-label={`重命名任务 ${task.id}`} onClick={()=>setOpen(true)}><Pencil size={17}/></button>{open&&<RenameDialog task={task} close={()=>setOpen(false)}/>}</>
}

function RenameDialog({task,close}:{task:AnalysisTask;close:()=>void}) {
  const [name,setName]=useState(task.name||task.manifest_path.split(/[\\/]/).pop()||'')
  const ref=useRef<HTMLDialogElement>(null),client=useQueryClient()
  const mutation=useMutation({mutationFn:()=>tasksApi.rename(task.id,name.trim()),onSuccess:updated=>{
    client.setQueryData(['task',task.id],updated)
    for(const key of ['tasks','task','dataset','datasets','results']) void client.invalidateQueries({queryKey:[key]})
    close()
  }})
  useEffect(()=>{ref.current?.showModal?.()},[])
  return <dialog ref={ref} open={typeof HTMLDialogElement.prototype.showModal!=='function'?true:undefined} className="cleanup-dialog" aria-label="重命名分析任务" onCancel={event=>{event.preventDefault();if(!mutation.isPending)close()}}>
    <h2>重命名分析任务</h2><p>仅修改显示名称，不改变任务 ID、输入文件或分析结果。</p>
    <form onSubmit={event=>{event.preventDefault();if(name.trim())mutation.mutate()}}><label className="cleanup-id-input">任务名称<input autoFocus value={name} maxLength={120} onChange={e=>setName(e.target.value)} disabled={mutation.isPending}/></label>
      {mutation.error&&<p role="alert" className="cleanup-error">{mutation.error.message}</p>}
      <div className="cleanup-buttons"><button className="btn secondary" type="button" disabled={mutation.isPending} onClick={close}>取消</button><button className="btn primary" type="submit" disabled={!name.trim()||mutation.isPending}>{mutation.isPending?'正在保存…':'保存名称'}</button></div>
    </form>
  </dialog>
}
