import type { NodeRegistry } from './registry'
import type {
  WorkflowDocument,
  WorkflowEdge,
  WorkflowValidationIssue,
  WorkflowValidationResult,
} from './types'

function issue(
  severity: WorkflowValidationIssue['severity'],
  code: string,
  message: string,
  nodeIds: string[] = [],
  edgeId?: string,
  confirmationKey?: string,
): WorkflowValidationIssue {
  return {
    severity,
    code,
    message,
    node_ids: nodeIds,
    edge_id: edgeId,
    confirmation_key: confirmationKey,
  }
}

function topologicalOrder(workflow: WorkflowDocument): { order: string[]; cyclic: boolean } {
  const ids = workflow.nodes.map(node => node.id)
  const known = new Set(ids)
  const incoming = new Map(ids.map(id => [id, 0]))
  const outgoing = new Map(ids.map(id => [id, [] as string[]]))
  workflow.edges.forEach(edge => {
    if (!known.has(edge.source_node) || !known.has(edge.target_node)) return
    incoming.set(edge.target_node, (incoming.get(edge.target_node) ?? 0) + 1)
    outgoing.get(edge.source_node)?.push(edge.target_node)
  })
  const ready = ids.filter(id => incoming.get(id) === 0)
  const order: string[] = []
  while (ready.length) {
    const current = ready.shift()!
    order.push(current)
    outgoing.get(current)?.forEach(target => {
      const count = (incoming.get(target) ?? 1) - 1
      incoming.set(target, count)
      if (count === 0) ready.push(target)
    })
  }
  return { order, cyclic: order.length !== ids.length }
}

export function connectionIssue(
  edge: WorkflowEdge,
  workflow: WorkflowDocument,
  registry: NodeRegistry,
): WorkflowValidationIssue | null {
  const source = workflow.nodes.find(node => node.id === edge.source_node)
  const target = workflow.nodes.find(node => node.id === edge.target_node)
  if (!source || !target) return issue('hard_error', 'unknown_edge_node', '连线引用了不存在的节点。', [edge.source_node, edge.target_node], edge.id)
  const sourceDefinition = registry.get(source.type)
  const targetDefinition = registry.get(target.type)
  const output = sourceDefinition?.outputs.find(port => port.id === edge.source_port)
  const input = targetDefinition?.inputs.find(port => port.id === edge.target_port)
  if (!output || !input) return issue('hard_error', 'unknown_port', '连线使用了注册表中不存在的端口。', [source.id, target.id], edge.id)
  if (!input.accepted_data_types.includes(output.data_type)) {
    return issue('hard_error', 'incompatible_port_types', `${output.data_type} 不能连接到 ${targetDefinition?.label}.${input.id}。`, [source.id, target.id], edge.id)
  }
  if (!input.accepted_scopes.includes(output.scope)) {
    return issue('hard_error', 'incompatible_data_scope', `${output.scope} 范围数据不能连接到 ${targetDefinition?.label}.${input.id}。`, [source.id, target.id], edge.id)
  }
  return null
}

export function validateWorkflow(workflow: WorkflowDocument, registry: NodeRegistry): WorkflowValidationResult {
  const issues: WorkflowValidationIssue[] = []
  const nodeIds = new Set<string>()
  workflow.nodes.forEach(node => {
    if (nodeIds.has(node.id)) issues.push(issue('hard_error', 'duplicate_node_id', '节点 ID 必须唯一。', [node.id]))
    nodeIds.add(node.id)
    if (!registry.has(node.type)) issues.push(issue('hard_error', 'unknown_node_type', `未注册的节点类型：${node.type}`, [node.id]))
  })

  const edgeIds = new Set<string>()
  const connected = new Map<string, number>()
  workflow.edges.forEach(edge => {
    if (edgeIds.has(edge.id)) issues.push(issue('hard_error', 'duplicate_edge_id', '连线 ID 必须唯一。', [], edge.id))
    edgeIds.add(edge.id)
    const compatibility = connectionIssue(edge, workflow, registry)
    if (compatibility) issues.push(compatibility)
    const key = `${edge.target_node}:${edge.target_port}`
    connected.set(key, (connected.get(key) ?? 0) + 1)
  })

  workflow.nodes.forEach(node => {
    const definition = registry.get(node.type)
    definition?.inputs.forEach(input => {
      const count = connected.get(`${node.id}:${input.id}`) ?? 0
      if (input.required && count === 0) issues.push(issue('hard_error', 'missing_required_input', `${definition.label}.${input.id} 需要上游连线。`, [node.id]))
      if (!input.multiple && count > 1) issues.push(issue('hard_error', 'multiple_connections_to_single_input', `${definition.label}.${input.id} 只允许一条输入连线。`, [node.id]))
    })
  })

  const topology = topologicalOrder(workflow)
  if (topology.cyclic) issues.push(issue('hard_error', 'cycle_detected', '工作流不能包含环路。'))

  // These are the backend's two explicitly allowed risk cases. They live only
  // in this validation adapter; components consume issues and never repeat rules.
  workflow.edges.forEach(edge => {
    const source = workflow.nodes.find(node => node.id === edge.source_node)
    const target = workflow.nodes.find(node => node.id === edge.target_node)
    const output = source && registry.get(source.type)?.outputs.find(port => port.id === edge.source_port)
    if (!source || !target || !output || edge.target_port !== 'reads') return
    let code: string | null = null
    let message = ''
    if (target.type === 'host_depletion' && output.data_type === 'paired_raw_reads') {
      code = 'host_depletion_without_fastp'
      message = '去宿主节点正在直接接收未经 fastp 清洗的原始 reads。'
    } else if (
      ['taxonomy', 'functional_annotation'].includes(target.type)
      && ['paired_raw_reads', 'paired_clean_reads'].includes(output.data_type)
    ) {
      code = 'annotation_without_host_depletion'
      message = '注释分析正在使用未去宿主的 reads，可能影响结果。'
    }
    if (code) {
      const key = `risk:${target.id}:${code}`
      issues.push({
        ...issue('warning', code, message, [target.id], edge.id, key),
        confirmed: workflow.accepted_risks.includes(key),
      })
    }
  })

  const hardErrors = issues.filter(item => item.severity === 'hard_error')
  const unconfirmed = issues.filter(item => item.severity === 'warning' && !item.confirmed)
  return {
    structurally_valid: hardErrors.length === 0,
    can_execute: hardErrors.length === 0 && unconfirmed.length === 0,
    topological_order: topology.order,
    issues,
  }
}
