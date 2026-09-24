import { FileDown, Maximize2, Minus, Plus, Terminal, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { NodeRegistry } from './registry'
import { connectionIssue } from './validation'
import type {
  CanvasPosition,
  WorkflowDocument,
  WorkflowEdge,
  WorkflowNodeRuntime,
} from './types'

interface Props {
  workflow: WorkflowDocument
  registry: NodeRegistry
  selectedNodeId: string | null
  runtime: Record<string, WorkflowNodeRuntime>
  invalidNodeIds: Set<string>
  onChange: (workflow: WorkflowDocument) => void
  onSelect: (id: string | null) => void
  onDropNode: (type: string, position: CanvasPosition) => void
  onConnectionError: (message: string) => void
}

const WIDTH = 184
const HEADER_HEIGHT = 48
const PORT_GAP = 23

export function WorkflowCanvas({ workflow, registry, selectedNodeId, runtime, invalidNodeIds, onChange, onSelect, onDropNode, onConnectionError }: Props) {
  const viewportRef = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  const [pending, setPending] = useState<{ nodeId: string; portId: string } | null>(null)

  const clampScale = (next: number) => setScale(Math.min(1.7, Math.max(.45, next)))
  const deleteNode = (nodeId: string) => {
    onChange({
      ...workflow,
      nodes: workflow.nodes.filter(node => node.id !== nodeId),
      edges: workflow.edges.filter(edge => edge.source_node !== nodeId && edge.target_node !== nodeId),
      accepted_risks: workflow.accepted_risks.filter(key => !key.startsWith(`risk:${nodeId}:`)),
    })
    onSelect(null)
  }

  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (!selectedNodeId || !['Backspace', 'Delete'].includes(event.key)) return
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLSelectElement || event.target instanceof HTMLTextAreaElement) return
      event.preventDefault()
      deleteNode(selectedNodeId)
    }
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  // deleteNode deliberately tracks the latest document through the dependencies.
  }, [selectedNodeId, workflow])

  const moveNode = (nodeId: string, event: React.PointerEvent) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    const start = { x: event.clientX, y: event.clientY }
    const node = workflow.nodes.find(item => item.id === nodeId)
    const origin = node?.position ?? { x: 0, y: 0 }
    const target = event.currentTarget
    const onMove = (move: PointerEvent) => {
      const position = {
        x: Math.max(0, origin.x + (move.clientX - start.x) / scale),
        y: Math.max(0, origin.y + (move.clientY - start.y) / scale),
      }
      onChange({ ...workflow, nodes: workflow.nodes.map(item => item.id === nodeId ? { ...item, position } : item) })
    }
    const onUp = () => {
      target.removeEventListener('pointermove', onMove as EventListener)
      target.removeEventListener('pointerup', onUp)
    }
    target.addEventListener('pointermove', onMove as EventListener)
    target.addEventListener('pointerup', onUp)
  }

  const connect = (targetNode: string, targetPort: string) => {
    if (!pending) return
    const edge: WorkflowEdge = {
      id: `edge-${crypto.randomUUID().slice(0, 8)}`,
      source_node: pending.nodeId,
      source_port: pending.portId,
      target_node: targetNode,
      target_port: targetPort,
    }
    const problem = connectionIssue(edge, workflow, registry)
    if (problem) onConnectionError(problem.message)
    else onChange({ ...workflow, edges: [...workflow.edges, edge], accepted_risks: [] })
    setPending(null)
  }

  const portPosition = (nodeId: string, portId: string, output: boolean): CanvasPosition => {
    const node = workflow.nodes.find(item => item.id === nodeId)
    const definition = node && registry.get(node.type)
    const ports = output ? definition?.outputs : definition?.inputs
    const index = Math.max(0, ports?.findIndex(port => port.id === portId) ?? 0)
    return {
      x: (node?.position?.x ?? 0) + (output ? WIDTH : 0),
      y: (node?.position?.y ?? 0) + HEADER_HEIGHT + 16 + index * PORT_GAP,
    }
  }

  return <section className="wf-canvas-shell">
    <div className="wf-canvas-toolbar">
      <span>{workflow.nodes.length} 节点 · {workflow.edges.length} 连线</span>
      {pending && <button className="wf-connecting" onClick={() => setPending(null)}><X size={13} />选择目标输入端口</button>}
      <div><button aria-label="缩小" onClick={() => clampScale(scale - .1)}><Minus /></button><b>{Math.round(scale * 100)}%</b><button aria-label="放大" onClick={() => clampScale(scale + .1)}><Plus /></button><button aria-label="重置缩放" onClick={() => setScale(1)}><Maximize2 /></button></div>
    </div>
    <div
      ref={viewportRef}
      className="wf-canvas-viewport"
      tabIndex={0}
      onClick={event => { if (event.target === event.currentTarget) onSelect(null) }}
      onWheel={event => { event.preventDefault(); clampScale(scale + (event.deltaY < 0 ? .08 : -.08)) }}
      onDragOver={event => { if (event.dataTransfer.types.includes('application/x-workflow-node')) event.preventDefault() }}
      onDrop={event => {
        const type = event.dataTransfer.getData('application/x-workflow-node')
        if (!type || !viewportRef.current) return
        event.preventDefault()
        const rect = viewportRef.current.getBoundingClientRect()
        onDropNode(type, { x: (event.clientX - rect.left) / scale, y: (event.clientY - rect.top) / scale })
      }}
    >
      <div className="wf-canvas-stage" style={{ transform: `scale(${scale})` }}>
        <svg className="wf-connections" aria-hidden="true">
          {workflow.edges.map(edge => {
            const from = portPosition(edge.source_node, edge.source_port, true)
            const to = portPosition(edge.target_node, edge.target_port, false)
            const bend = Math.max(60, Math.abs(to.x - from.x) * .45)
            return <g key={edge.id} className={selectedNodeId && [edge.source_node, edge.target_node].includes(selectedNodeId) ? 'active' : ''}>
              <path d={`M ${from.x} ${from.y} C ${from.x + bend} ${from.y}, ${to.x - bend} ${to.y}, ${to.x} ${to.y}`} />
              <g className="wf-edge-delete" role="button" tabIndex={0} onClick={() => onChange({ ...workflow, edges: workflow.edges.filter(item => item.id !== edge.id), accepted_risks: [] })}>
                <title>删除连线</title><circle cx={(from.x + to.x) / 2} cy={(from.y + to.y) / 2} r="8" /><path d={`M ${(from.x + to.x) / 2 - 3} ${(from.y + to.y) / 2} h 6`} />
              </g>
            </g>
          })}
        </svg>
        {workflow.nodes.map(node => {
          const definition = registry.get(node.type)
          if (!definition) return null
          const state = runtime[node.id]
          const selected = selectedNodeId === node.id
          return <article
            className={`wf-node status-${state?.status ?? 'pending'} ${selected ? 'selected' : ''} ${invalidNodeIds.has(node.id) ? 'invalid' : ''}`}
            style={{ left: node.position?.x ?? 0, top: node.position?.y ?? 0 }}
            key={node.id}
            onClick={event => { event.stopPropagation(); onSelect(node.id) }}
          >
            <header onPointerDown={event => moveNode(node.id, event)}>
              <i /><span><b>{definition.label}</b><small>{node.id}</small></span>
              <button aria-label={`删除 ${definition.label}`} onPointerDown={event => event.stopPropagation()} onClick={() => deleteNode(node.id)}><Trash2 /></button>
            </header>
            <div className="wf-node-ports">
              <div>{definition.inputs.map(port => <button key={port.id} className="input" title={port.accepted_data_types.join(', ')} onClick={event => { event.stopPropagation(); connect(node.id, port.id) }}><i />{port.id}</button>)}</div>
              <div>{definition.outputs.map(port => <button key={port.id} className={`output ${pending?.nodeId === node.id && pending.portId === port.id ? 'pending' : ''}`} title={`${port.data_type} (${port.scope})`} onClick={event => { event.stopPropagation(); setPending({ nodeId: node.id, portId: port.id }) }}>{port.id}<i /></button>)}</div>
            </div>
            {state && <footer><span><i />{state.status === 'running' && state.progress != null ? `${Math.round(state.progress)}%` : state.status}</span><div>{state.log_url && <a href={state.log_url} target="_blank" rel="noreferrer" title="查看日志"><Terminal /></a>}{state.result_url && <a href={state.result_url} download title="下载结果"><FileDown /></a>}</div></footer>}
          </article>
        })}
      </div>
    </div>
  </section>
}
