import { describe, expect, it } from 'vitest'
import { autoLayout } from './layout'
import type { WorkflowDocument } from './types'

describe('autoLayout', () => {
  it('places downstream nodes in horizontal analysis columns and branches in lanes', () => {
    const workflow: WorkflowDocument = {
      schema_version: '1.0', accepted_risks: [],
      nodes: ['input', 'left', 'right', 'report'].map(id => ({ id, type: 'test', parameters: {} })),
      edges: [
        { id: 'e1', source_node: 'input', source_port: 'out', target_node: 'left', target_port: 'in' },
        { id: 'e2', source_node: 'input', source_port: 'out', target_node: 'right', target_port: 'in' },
        { id: 'e3', source_node: 'left', source_port: 'out', target_node: 'report', target_port: 'in' },
        { id: 'e4', source_node: 'right', source_port: 'out', target_node: 'report', target_port: 'in' },
      ],
    }
    const laidOut = autoLayout(workflow)
    const positions = Object.fromEntries(laidOut.nodes.map(node => [node.id, node.position!]))
    expect(positions.input.x).toBeLessThan(positions.left.x)
    expect(positions.left.x).toBe(positions.right.x)
    expect(positions.left.y).not.toBe(positions.right.y)
    expect(positions.report.x).toBeGreaterThan(positions.left.x)
  })
})
