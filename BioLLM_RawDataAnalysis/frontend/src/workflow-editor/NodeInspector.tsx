import { AlertTriangle, CheckCircle2, Cpu, Database, FileDown, Info, SlidersHorizontal, Terminal } from 'lucide-react'
import type { NodeRegistry } from './registry'
import type { JsonPrimitive, WorkflowDocument, WorkflowNodeRuntime, WorkflowValidationResult } from './types'

interface Props {
  workflow: WorkflowDocument
  registry: NodeRegistry
  selectedNodeId: string | null
  validation: WorkflowValidationResult
  runtime?: WorkflowNodeRuntime
  onChange: (workflow: WorkflowDocument) => void
  onFocusNode: (id: string) => void
}

const PARAMETER_LABELS: Record<string, string> = {
  threads: '线程数', memory_gb: '内存 (GB)', assembler: '组装器',
  qualified_quality_phred: '质量阈值', length_required: '最低保留长度',
  filter_mode: '过滤模式', read_length: 'Reads 长度', completeness: '最低完整度',
  contamination: '最高污染度',
}

export function NodeInspector({ workflow, registry, selectedNodeId, validation, runtime, onChange, onFocusNode }: Props) {
  const node = workflow.nodes.find(item => item.id === selectedNodeId)
  const definition = node && registry.get(node.type)
  const updateParameter = (key: string, value: JsonPrimitive) => {
    if (!node) return
    onChange({
      ...workflow,
      nodes: workflow.nodes.map(item => item.id === node.id ? { ...item, parameters: { ...item.parameters, [key]: value } } : item),
    })
  }
  const selectedIssues = validation.issues.filter(item => !node || !item.node_ids.length || item.node_ids.includes(node.id))

  return <aside className="wf-inspector" aria-label="节点配置">
    <header><div className="wf-section-icon"><SlidersHorizontal size={18} /></div><div><b>{definition?.label ?? '工作流检查'}</b><span>{node?.id ?? '选择节点以编辑配置'}</span></div></header>
    {node && definition ? <>
      <section><h3>节点参数</h3>
        {!Object.keys(definition.parameters_schema).length && <p className="wf-empty">此节点无可配置参数。</p>}
        {Object.entries(definition.parameters_schema).map(([key, schema]) => {
          const value = node.parameters[key] ?? schema.default ?? ''
          const label = schema.title ?? PARAMETER_LABELS[key] ?? key
          if (schema.type === 'boolean') return <label className="wf-toggle" key={key}><span><b>{label}</b><small>{schema.description}</small></span><input type="checkbox" checked={Boolean(value)} onChange={event => updateParameter(key, event.target.checked)} /></label>
          if (schema.enum) return <label className="wf-field" key={key}><span>{label}</span><select value={String(value)} onChange={event => {
            const next = schema.type === 'integer' || schema.type === 'number' ? Number(event.target.value) : event.target.value
            updateParameter(key, next)
          }}>{schema.enum.map(option => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select></label>
          return <label className="wf-field" key={key}><span>{label}</span><input
            type={schema.type === 'integer' || schema.type === 'number' ? 'number' : 'text'}
            value={String(value)} min={schema.minimum} max={schema.maximum}
            onChange={event => updateParameter(key, schema.type === 'integer' || schema.type === 'number' ? Number(event.target.value) : event.target.value)}
          /></label>
        })}
      </section>
      <section><h3><Cpu />资源配置</h3><div className="wf-resource-grid"><div><span>CPU</span><b>{String(node.parameters.threads ?? definition.resources.default_cpus)} 核</b></div><div><span>内存</span><b>{String(node.parameters.memory_gb ?? definition.resources.default_memory_gb)} GB</b></div></div><p className="wf-help">可调整资源仅来自注册表参数；其余由服务器默认配置。</p></section>
      <section><h3><Database />数据库要求</h3>{definition.database_requirements.length ? <ul className="wf-databases">{definition.database_requirements.map(database => <li key={database}><Database /><code>{database}</code><span>服务器管理</span></li>)}</ul> : <p className="wf-empty">无额外数据库要求。</p>}</section>
      {runtime && <section><h3>运行入口</h3><div className="wf-runtime-actions">{runtime.log_url && <a href={runtime.log_url} target="_blank" rel="noreferrer"><Terminal />查看节点日志</a>}{runtime.result_url && <a href={runtime.result_url} download><FileDown />下载节点结果</a>}{!runtime.log_url && !runtime.result_url && <span>任务开始后显示</span>}</div></section>}
    </> : <div className="wf-inspector-placeholder"><SlidersHorizontal /><b>选中画布中的节点</b><p>可查看参数、计算资源、数据库要求和节点校验结果。</p></div>}
    <section className="wf-validation-panel"><h3>{validation.structurally_valid ? <CheckCircle2 /> : <AlertTriangle />}校验信息 <span>{selectedIssues.length}</span></h3>
      {!selectedIssues.length && <p className="wf-validation-ok"><CheckCircle2 />当前{node ? '节点' : '画布'}未发现问题</p>}
      {selectedIssues.map((item, index) => <button key={`${item.code}-${item.edge_id ?? index}`} className={item.severity} onClick={() => item.node_ids[0] && onFocusNode(item.node_ids[0])}>{item.severity === 'hard_error' ? <AlertTriangle /> : <Info />}<span><b>{item.severity === 'hard_error' ? '阻断错误' : item.confirmed ? '已接受风险' : '待确认风险'}</b><small>{item.message}</small></span></button>)}
    </section>
  </aside>
}
