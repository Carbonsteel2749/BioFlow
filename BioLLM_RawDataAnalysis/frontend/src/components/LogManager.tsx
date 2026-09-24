import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { tasksApi } from '../api/tasks'
import type { LogCleanupPreview } from '../api/tasks'
import { formatBytes } from '../artifact-center/catalog'
import './result-tools.css'

export function LogManager({ taskId }: { taskId: string }) {
  const [open,setOpen]=useState(false), [selected,setSelected]=useState<string[]>([])
  const [preview,setPreview]=useState<LogCleanupPreview | null>(null)
  const [content,setContent]=useState(''), [message,setMessage]=useState(''), [busy,setBusy]=useState(false)
  const query=useQuery({queryKey:['log-files',taskId],queryFn:()=>tasksApi.logFiles(taskId),enabled:open,retry:false})
  const files=Array.isArray(query.data?.files) ? query.data.files : []
  const run=async (action:()=>Promise<void>)=>{setBusy(true);setMessage('');try{await action()}catch(error){setMessage(error instanceof Error ? error.message : '操作失败，请刷新重试');setPreview(null)}finally{setBusy(false)}}
  return <section className="panel result-tools log-manager">
    <header><div><h2>日志管理</h2><p>日志清理与缓存清理分开；受保护的排错证据和分析数据不删除。</p></div><button className="btn secondary" onClick={()=>{setOpen(v=>!v);setPreview(null)}}>{open?'收起日志':'管理日志'}</button></header>
    {open && <>
      {query.isLoading ? <p>正在读取日志清单…</p> : query.error ? <p role="alert">日志清单读取失败，请刷新重试。</p> : <>
        <p>{query.data?.scope}</p>
        <button className="btn secondary" disabled={busy} onClick={()=>{setPreview(null);setSelected([]);void query.refetch()}}>刷新日志列表</button>
        <div className="log-table"><table><thead><tr><th>选择</th><th>日志 / 阶段</th><th>大小 / 时间</th><th>状态</th><th>查看</th></tr></thead><tbody>{files.map(file=><tr key={file.id}>
          <td><input type="checkbox" aria-label={`选择 ${file.name}`} disabled={!file.selectable || busy || !!preview} checked={selected.includes(file.id)} onChange={e=>{setPreview(null);setSelected(previous=>e.target.checked?[...previous,file.id]:previous.filter(id=>id!==file.id))}} /></td>
          <td><b>{file.name}</b><small>{file.step}</small></td><td>{formatBytes(file.size_bytes)}<small>{file.modified_at}</small></td><td>{file.reason || '可选择清理'}</td>
          <td><button disabled={busy} onClick={()=>run(async()=>{setContent((await tasksApi.logFile(taskId,file.id)).text)})}>预览</button></td>
        </tr>)}</tbody></table></div>
        {!files.length && <p>暂无日志文件。</p>}
        {content && <pre className="managed-log-preview">{content}</pre>}
        {!preview ? <button className="btn secondary" disabled={!selected.length || busy} onClick={()=>run(async()=>{setPreview(await tasksApi.logCleanupPreview(taskId,selected))})}>预览清理范围</button> : <div className="log-delete-confirm" role="group" aria-label="日志删除确认">
          <b>将删除 {preview.files.length} 个日志，约 {formatBytes(preview.estimated_bytes)}。删除后无法恢复。</b>
          <ul>{preview.files.map(file=><li key={file.id}>{file.name}</li>)}</ul>
          <button className="btn secondary" disabled={busy} onClick={()=>setPreview(null)}>返回选择</button>
          <button className="btn danger" disabled={busy} onClick={()=>run(async()=>{const result=await tasksApi.logCleanup(taskId,selected,preview.token);setPreview(null);setSelected([]);setContent('');setMessage(`已清理 ${result.removed_count} 个日志`);await query.refetch()})}>确认删除日志</button>
        </div>}
      </>}
      {message && <p role="status">{message}</p>}
    </>}
  </section>
}
