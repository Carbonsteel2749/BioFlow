import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'
import { tasksApi } from '../api/tasks'
import type { AnalysisTask } from '../types'
import './task-cleanup.css'

function bytes(value: number) {
  if (value < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let amount = value / 1024, index = 0
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index++ }
  return `${amount.toFixed(1)} ${units[index]}`
}

export function TaskCleanupActions({ task }: { task: AnalysisTask }) {
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState('')
  const disabled = ['queued', 'validating', 'running'].includes(task.status)
  return <div className="task-cleanup-action">
    <button className="icon-btn cleanup-trigger" aria-label={`删除或清理任务 ${task.id}`} title={disabled ? '请先取消任务并等待进程退出' : '删除任务或清理中间文件'} disabled={disabled} onClick={() => { setMessage(''); setOpen(true) }}><Trash2 size={17} /></button>
    {message && <span role="status" className="cleanup-done">{message}</span>}
    {open && <CleanupDialog task={task} close={() => setOpen(false)} done={setMessage} />}
  </div>
}

function CleanupDialog({ task, close, done }: { task: AnalysisTask; close: () => void; done: (text: string) => void }) {
  const [mode, setMode] = useState<'cache' | 'delete'>('cache')
  const [confirmed, setConfirmed] = useState(false)
  const [typedId, setTypedId] = useState('')
  const [deleteDataset,setDeleteDataset]=useState(false)
  const sync=mode==='delete'&&deleteDataset
  const dialog = useRef<HTMLDialogElement>(null)
  const client = useQueryClient()
  const preview = useQuery({ queryKey: ['task-cleanup', task.id, mode,sync], queryFn: () => tasksApi.cleanupPreview(task.id, mode,sync), retry: false, staleTime: 0, gcTime: 0 })
  const mutation = useMutation({
    mutationFn: () => tasksApi.cleanup(task.id, mode, preview.data!.token,sync),
    onSuccess: result => {
      done(mode === 'cache' ? `缓存已清理，预计释放 ${bytes(result.removed_bytes)}` : '任务已删除')
      void client.invalidateQueries({ queryKey: ['tasks'] })
      void client.invalidateQueries({ queryKey: ['task', task.id] })
      for(const key of ['datasets','dataset','unpaired-uploads','results','result-index']) void client.invalidateQueries({queryKey:[key]})
      close()
    },
  })
  useEffect(() => {
    dialog.current?.showModal?.()
  }, [])
  const ready = confirmed && !preview.isFetching && !preview.error && !!preview.data && (mode === 'cache' || typedId === task.id)
  return <dialog ref={dialog} open={typeof HTMLDialogElement.prototype.showModal !== 'function' ? true : undefined} className="cleanup-dialog" aria-labelledby={`cleanup-title-${task.id}`} onCancel={event => { event.preventDefault(); if (!mutation.isPending) close() }}>
    <h2 id={`cleanup-title-${task.id}`}>删除任务 / 清理空间</h2>
    <p className="cleanup-task-id">任务 ID：<code>{task.id}</code></p>
    <fieldset disabled={mutation.isPending} className="cleanup-choices"><legend>选择操作范围</legend>
      <label><input type="radio" name={`cleanup-${task.id}`} checked={mode === 'cache'} onChange={() => { setMode('cache'); setConfirmed(false); mutation.reset() }} /><span><b>清理中间文件</b><small>仅删除计算缓存；保留任务、日志和最终结果，无法再利用这些缓存断点续跑。</small></span></label>
      <label><input type="radio" name={`cleanup-${task.id}`} checked={mode === 'delete'} onChange={() => { setMode('delete'); setConfirmed(false); mutation.reset() }} /><span><b>删除任务及专属文件</b><small>删除任务记录、计算缓存、日志和结果，操作不可恢复。</small></span></label>
    </fieldset>
    {mode==='delete'&&<label className="cleanup-confirm"><input type="checkbox" checked={deleteDataset} disabled={mutation.isPending} onChange={e=>{setDeleteDataset(e.target.checked);setConfirmed(false);mutation.reset()}}/>同时删除关联数据与样本（仅无其他引用时）</label>}
    <p className="cleanup-preserved">{sync?'勾选后仅清理无其他引用的平台托管数据；历史或无法核实的数据会保留。':'原始 FASTQ 和上传样本清单均保留，不会删除。'} 已登记的跨模块引用会阻止删除结果；未登记的手工使用无法完整识别。</p>
    {!preview.isFetching&&preview.data?.dataset_cleanup&&<div className="cleanup-preserved"><b>{preview.data.dataset_cleanup.will_delete?'将同步删除数据集、样本清单和托管原始 FASTQ':'关联数据将保留，仅删除任务'}</b>{preview.data.dataset_cleanup.reasons.map(reason=><p key={reason}>{reason}</p>)}</div>}
    {preview.isFetching ? <p role="status">正在核实进程、引用和可释放空间…</p> : preview.error ? <p role="alert" className="cleanup-error">{preview.error.message}</p> : preview.data && <section className="cleanup-preview"><strong>预计可释放：<span>约 {bytes(preview.data.estimated_bytes)}</span></strong><small>按文件占用块估算，共享硬链接不计入；实际释放量可能不同。</small><ul>{preview.data.scope.map(path => <li key={path}><code>{path}</code></li>)}</ul></section>}
    <button type="button" className="cleanup-recheck" disabled={preview.isFetching || mutation.isPending} onClick={() => { setConfirmed(false); void preview.refetch() }}>重新核实范围</button>
    {mode === 'delete' && <label className="cleanup-id-input">输入完整任务 ID<input aria-label="输入完整任务 ID" value={typedId} onChange={event => setTypedId(event.target.value)} autoComplete="off" disabled={mutation.isPending} /></label>}
    <label className="cleanup-confirm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={mutation.isPending} />我已确认范围，了解此操作不可恢复</label>
    {mutation.error && <p role="alert" className="cleanup-error">{mutation.error.message}</p>}
    <div className="cleanup-buttons"><button className="btn secondary" disabled={mutation.isPending} onClick={close}>返回</button><button className="btn danger-outline" disabled={!ready || mutation.isPending} onClick={() => mutation.mutate()}>{mutation.isPending ? '正在清理，请勿关闭…' : mode === 'cache' ? '确认清理' : '确认删除'}</button></div>
  </dialog>
}
