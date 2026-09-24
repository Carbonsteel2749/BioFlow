import type { WorkflowDocument } from './types'

const NODE_WIDTH = 184
const X_GAP = 105
const Y_GAP = 42

export function autoLayout(workflow: WorkflowDocument): WorkflowDocument {
  const incoming = new Map(workflow.nodes.map(node => [node.id, 0]))
  const outgoing = new Map(workflow.nodes.map(node => [node.id, [] as string[]]))
  workflow.edges.forEach(edge => {
    if (!incoming.has(edge.source_node) || !incoming.has(edge.target_node)) return
    incoming.set(edge.target_node, (incoming.get(edge.target_node) ?? 0) + 1)
    outgoing.get(edge.source_node)?.push(edge.target_node)
  })

  const queue = workflow.nodes.filter(node => incoming.get(node.id) === 0).map(node => node.id)
  const depth = new Map(queue.map(id => [id, 0]))
  while (queue.length) {
    const current = queue.shift()!
    outgoing.get(current)?.forEach(target => {
      depth.set(target, Math.max(depth.get(target) ?? 0, (depth.get(current) ?? 0) + 1))
      const remaining = (incoming.get(target) ?? 1) - 1
      incoming.set(target, remaining)
      if (remaining === 0) queue.push(target)
    })
  }

  // Cyclic/unconnected leftovers still get a stable lane instead of disappearing.
  workflow.nodes.forEach((node, index) => { if (!depth.has(node.id)) depth.set(node.id, index) })
  const lanes = new Map<number, string[]>()
  workflow.nodes.forEach(node => {
    const column = depth.get(node.id) ?? 0
    lanes.set(column, [...(lanes.get(column) ?? []), node.id])
  })

  return {
    ...workflow,
    nodes: workflow.nodes.map(node => {
      const column = depth.get(node.id) ?? 0
      const lane = lanes.get(column) ?? []
      const row = lane.indexOf(node.id)
      return {
        ...node,
        position: { x: 54 + column * (NODE_WIDTH + X_GAP), y: 70 + row * (112 + Y_GAP) },
      }
    }),
  }
}
