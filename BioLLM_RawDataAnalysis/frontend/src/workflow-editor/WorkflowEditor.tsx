import { AlertTriangle, Check, LayoutDashboard, LoaderCircle, Play, Save, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { workflowsApi } from '../api/workflows'
import { autoLayout } from './layout'
import { NodeInspector } from './NodeInspector'
import { NodeLibrary } from './NodeLibrary'
import { createNode, createRegistry } from './registry'
import type {
  NodeRegistryResponse,
  WorkflowDocument,
  WorkflowNodeRuntime,
  WorkflowTemplateSummary,
} from './types'
import { validateWorkflow } from './validation'
import { WorkflowCanvas } from './WorkflowCanvas'
import './workflow-editor.css'

const EMPTY_WORKFLOW: WorkflowDocument = {
  schema_version: '1.0',
  nodes: [],
  edges: [],
  accepted_risks: [],
}

export interface WorkflowEditorProps {
  initialWorkflow?: WorkflowDocument
  initialRegistry?: NodeRegistryResponse
  runtime?: Record<string, WorkflowNodeRuntime>
  onSave?: (workflow: WorkflowDocument) => void | Promise<void>
  onRun?: (workflow: WorkflowDocument) => void | Promise<void>
}

function downloadWorkflow(workflow: WorkflowDocument) {
  const content = JSON.stringify(workflow, null, 2)
  const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = 'workflow.json'
  anchor.click()
  URL.revokeObjectURL(url)
}

export function WorkflowEditor({ initialWorkflow, initialRegistry, runtime = {}, onSave, onRun }: WorkflowEditorProps) {
  const [workflow, setWorkflow] = useState<WorkflowDocument>(initialWorkflow ?? EMPTY_WORKFLOW)
  const [registryResponse, setRegistryResponse] = useState<NodeRegistryResponse | null>(initialRegistry ?? null)
  const [templates, setTemplates] = useState<WorkflowTemplateSummary[]>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [loading, setLoading] = useState(!initialRegistry)
  const [loadingTemplate, setLoadingTemplate] = useState(false)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [confirmRisks, setConfirmRisks] = useState(false)

  useEffect(() => {
    let active = true
    Promise.all([
      initialRegistry ? Promise.resolve(initialRegistry) : workflowsApi.registry(),
      workflowsApi.templates().catch(() => []),
    ]).then(([registry, templateList]) => {
      if (!active) return
      setRegistryResponse(registry)
      setTemplates(templateList)
    }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : '无法读取节点注册表')
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [initialRegistry])

  const registry = useMemo(() => createRegistry(registryResponse ?? { registry_version: '', nodes: [] }), [registryResponse])
  const validation = useMemo(() => validateWorkflow(workflow, registry), [workflow, registry])
  const invalidNodeIds = useMemo(() => new Set(validation.issues.filter(item => item.severity === 'hard_error').flatMap(item => item.node_ids)), [validation])
  const unconfirmedRisks = validation.issues.filter(item => item.severity === 'warning' && !item.confirmed && item.confirmation_key)

  const selectProblem = () => {
    const problem = validation.issues.find(item => item.severity === 'hard_error')
    if (problem?.node_ids[0]) setSelectedNodeId(problem.node_ids[0])
    setNotice(problem?.message ?? '工作流存在阻断错误。')
  }

  const save = async () => {
    if (!workflow.nodes.length) { setNotice('请先向画布添加节点。'); return }
    setSaving(true)
    try {
      if (onSave) await onSave(workflow)
      else downloadWorkflow(workflow)
      setNotice(onSave ? '工作流已保存。' : 'Workflow JSON 已导出。')
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : '保存失败')
    } finally { setSaving(false) }
  }

  const execute = async (nextWorkflow = workflow) => {
    setRunning(true)
    try {
      await onRun?.(nextWorkflow)
      setNotice(onRun ? '工作流已提交运行。' : '工作流校验通过，等待集成运行回调。')
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : '提交运行失败')
    } finally { setRunning(false) }
  }

  const run = () => {
    if (!validation.structurally_valid) { selectProblem(); return }
    if (unconfirmedRisks.length) { setConfirmRisks(true); return }
    void execute()
  }

  const acceptAndRun = () => {
    const accepted = [...new Set([
      ...workflow.accepted_risks,
      ...unconfirmedRisks.flatMap(item => item.confirmation_key ? [item.confirmation_key] : []),
    ])]
    const next = { ...workflow, accepted_risks: accepted }
    setWorkflow(next)
    setConfirmRisks(false)
    void execute(next)
  }

  const loadTemplate = async (summary: WorkflowTemplateSummary) => {
    setLoadingTemplate(true)
    try {
      const template = await workflowsApi.template(summary.template_id, summary.version)
      setWorkflow(autoLayout(template.workflow))
      setSelectedNodeId(null)
      setNotice(`已加载模板：${summary.name}`)
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : '模板加载失败')
    } finally { setLoadingTemplate(false) }
  }

  if (loading) return <div className="wf-loading"><LoaderCircle className="spin" /><b>正在读取服务器节点注册表…</b></div>
  if (!registryResponse) return <div className="wf-load-error"><AlertTriangle /><b>节点注册表不可用</b><p>{error}</p></div>

  return <div className="workflow-editor">
    <div className="wf-editor-header">
      <div><span className="wf-eyebrow">WORKFLOW EDITOR · REGISTRY {registryResponse.registry_version}</span><h1>可视化分析工作流</h1><p>节点、端口与参数均由后端注册表驱动</p></div>
      <div className="wf-header-actions">
        <button className="wf-btn secondary" onClick={() => setWorkflow(autoLayout(workflow))} disabled={!workflow.nodes.length}><LayoutDashboard />自动整理</button>
        <button className="wf-btn secondary" onClick={() => void save()} disabled={saving || !workflow.nodes.length}>{saving ? <LoaderCircle className="spin" /> : <Save />}保存 JSON</button>
        <button className="wf-btn primary" onClick={run} disabled={running || !workflow.nodes.length}>{running ? <LoaderCircle className="spin" /> : <Play />}运行工作流</button>
      </div>
    </div>
    {notice && <div className="wf-notice"><span>{notice}</span><button onClick={() => setNotice(null)}><X /></button></div>}
    <div className="wf-editor-grid">
      <NodeLibrary definitions={registryResponse.nodes} templates={templates} loadingTemplate={loadingTemplate} onLoadTemplate={loadTemplate} />
      <WorkflowCanvas
        workflow={workflow}
        registry={registry}
        selectedNodeId={selectedNodeId}
        runtime={runtime}
        invalidNodeIds={invalidNodeIds}
        onChange={setWorkflow}
        onSelect={setSelectedNodeId}
        onDropNode={(type, position) => {
          const definition = registry.get(type)
          if (!definition) return
          const node = createNode(definition, position)
          setWorkflow(current => ({ ...current, nodes: [...current.nodes, node], accepted_risks: [] }))
          setSelectedNodeId(node.id)
        }}
        onConnectionError={setNotice}
      />
      <NodeInspector workflow={workflow} registry={registry} selectedNodeId={selectedNodeId} validation={validation} runtime={selectedNodeId ? runtime[selectedNodeId] : undefined} onChange={setWorkflow} onFocusNode={setSelectedNodeId} />
    </div>
    <footer className="wf-statusbar">
      <span className={validation.structurally_valid ? 'valid' : 'invalid'}>{validation.structurally_valid ? <Check /> : <AlertTriangle />}{validation.structurally_valid ? '结构校验通过' : `${validation.issues.filter(item => item.severity === 'hard_error').length} 个阻断错误`}</span>
      <span>{validation.issues.filter(item => item.severity === 'warning').length} 个风险提示</span>
      <span>{workflow.accepted_risks.length} 个已接受风险</span>
    </footer>
    {confirmRisks && <div className="wf-modal-layer" role="dialog" aria-modal="true" aria-label="风险确认"><div className="wf-risk-modal"><header><div><AlertTriangle /></div><span><b>运行前需要二次确认</b><small>这些连接可以执行，但可能影响分析质量。</small></span></header><ul>{unconfirmedRisks.map(item => <li key={item.confirmation_key ?? item.code}><AlertTriangle /><span><b>{item.code}</b><p>{item.message}</p></span></li>)}</ul><p className="wf-risk-note">{`确认后将 ${unconfirmedRisks.length} 个风险键写入 Workflow JSON 的 accepted_risks。`}</p><footer><button className="wf-btn secondary" onClick={() => setConfirmRisks(false)}>返回修改</button><button className="wf-btn warning" onClick={acceptAndRun}><Check />我已了解，继续运行</button></footer></div></div>}
  </div>
}
