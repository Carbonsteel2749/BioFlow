import { describe, expect, it } from 'vitest'
import { createRegistry } from './registry'
import type { NodeRegistryResponse, WorkflowDocument } from './types'
import { validateWorkflow } from './validation'

const response: NodeRegistryResponse = {
  registry_version: 'test',
  nodes: [
    {
      type: 'source', version: '1', label: 'Source', category: 'input', inputs: [],
      outputs: [{ id: 'reads', data_type: 'paired_raw_reads', scope: 'sample' }],
      parameters_schema: {}, resources: { default_cpus: 1, default_memory_gb: 1 }, database_requirements: [],
    },
    {
      type: 'host_depletion', version: '1', label: 'Host', category: 'preprocessing',
      inputs: [{ id: 'reads', accepted_data_types: ['paired_raw_reads'], accepted_scopes: ['sample'], required: true, multiple: false, collect: false }],
      outputs: [{ id: 'reads', data_type: 'paired_host_removed_reads', scope: 'sample' }],
      parameters_schema: {}, resources: { default_cpus: 4, default_memory_gb: 8 }, database_requirements: ['host.db'],
    },
  ],
}

const workflow: WorkflowDocument = {
  schema_version: '1.0', accepted_risks: [],
  nodes: [
    { id: 'input', type: 'source', parameters: {}, position: { x: 0, y: 0 } },
    { id: 'host', type: 'host_depletion', parameters: {}, position: { x: 200, y: 0 } },
  ],
  edges: [{ id: 'e1', source_node: 'input', source_port: 'reads', target_node: 'host', target_port: 'reads' }],
}

describe('validateWorkflow', () => {
  it('requires explicit confirmation for allowed risky connections', () => {
    const result = validateWorkflow(workflow, createRegistry(response))
    expect(result.structurally_valid).toBe(true)
    expect(result.can_execute).toBe(false)
    expect(result.issues[0].confirmation_key).toBe('risk:host:host_depletion_without_fastp')
  })

  it('allows execution after the risk key is accepted', () => {
    const accepted = { ...workflow, accepted_risks: ['risk:host:host_depletion_without_fastp'] }
    expect(validateWorkflow(accepted, createRegistry(response)).can_execute).toBe(true)
  })

  it('reports and locates missing required inputs', () => {
    const disconnected = { ...workflow, edges: [] }
    const result = validateWorkflow(disconnected, createRegistry(response))
    expect(result.can_execute).toBe(false)
    expect(result.issues).toContainEqual(expect.objectContaining({ code: 'missing_required_input', node_ids: ['host'] }))
  })
})
