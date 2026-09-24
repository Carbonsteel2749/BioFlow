import { CheckCircle2, Database, LoaderCircle } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { workflowsApi } from '../api/workflows'
import { UploadPanel } from '../components/UploadPanel'
import type {
  DynamicWorkflowRun,
  WorkflowDocument,
  WorkflowNodeRuntime,
} from './types'
import { WorkflowEditor } from './WorkflowEditor'

const ACTIVE_STATUSES = new Set(['queued', 'running'])

export function WorkflowStudioPage() {
  const [manifestPath, setManifestPath] = useState('')
  const [sampleCount, setSampleCount] = useState(0)
  const [run, setRun] = useState<DynamicWorkflowRun | null>(null)

  useEffect(() => {
    if (!run || !ACTIVE_STATUSES.has(run.status)) return
    let active = true
    const refresh = async () => {
      try {
        const latest = await workflowsApi.run(run.task_id)
        if (active) setRun(latest)
      } catch { /* submission errors are surfaced by WorkflowEditor */ }
    }
    const timer = window.setInterval(() => void refresh(), 2000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [run?.task_id, run?.status])

  const runtime = useMemo<Record<string, WorkflowNodeRuntime>>(
    () => Object.fromEntries(
      (run?.nodes ?? []).map(node => [node.node_id, {
        status: node.status,
        progress: node.progress,
        log_url: `/api/workflows/runs/${run?.task_id}/logs`,
      }]),
    ),
    [run],
  )

  const submit = async (workflow: WorkflowDocument) => {
    if (!manifestPath) {
      throw new Error('请先上传双端 FASTQ 并生成样本清单。')
    }
    setRun(await workflowsApi.createRun(manifestPath, workflow))
  }

  return <div className="workflow-studio-page">
    <section className="workflow-studio-intro">
      <div>
        <span className="eyebrow">DIY PIPELINE</span>
        <h1>节点式分析工作流</h1>
        <p>拖动节点并连接端口，自定义宏基因组分析路径；不合理连接会先提示并要求确认。</p>
      </div>
    </section>
    <section className="panel workflow-data-panel">
      <header><Database /><div><b>本次分析数据</b><span>请先上传双端 FASTQ，再设计或载入分析流程。</span></div></header>
      <UploadPanel supported onManifest={(path, samples) => {
        setManifestPath(path)
        setSampleCount(samples)
        setRun(null)
      }} />
      {manifestPath && <div className="workflow-manifest-ready"><CheckCircle2 /><b>已生成 {sampleCount} 个样本的输入清单</b><code>{manifestPath.split('/').pop()}</code></div>}
      {run && <div className="workflow-run-summary"><span>{ACTIVE_STATUSES.has(run.status) && <LoaderCircle className="spin" />}任务 <code>{run.task_id}</code></span><b>{run.status}</b><small>{run.nodes.filter(node => node.status === 'succeeded').length}/{run.nodes.length} 个节点完成</small></div>}
    </section>
    <WorkflowEditor runtime={runtime} onRun={submit} />
  </div>
}
