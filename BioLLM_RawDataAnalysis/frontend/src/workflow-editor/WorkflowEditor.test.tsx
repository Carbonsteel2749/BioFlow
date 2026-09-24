import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { NodeRegistryResponse, WorkflowDocument } from './types'
import { WorkflowEditor } from './WorkflowEditor'

const registry: NodeRegistryResponse = {
  registry_version: '1.0.0',
  nodes: [
    {
      type: 'fastq_input', version: '1', label: 'FASTQ 输入', category: 'input', inputs: [],
      outputs: [{ id: 'reads', data_type: 'paired_raw_reads', scope: 'sample' }], parameters_schema: {},
      resources: { default_cpus: 1, default_memory_gb: 1 }, database_requirements: [],
    },
    {
      type: 'host_depletion', version: '1', label: '去除宿主序列', category: 'preprocessing',
      inputs: [{ id: 'reads', accepted_data_types: ['paired_raw_reads'], accepted_scopes: ['sample'], required: true, multiple: false, collect: false }],
      outputs: [{ id: 'reads', data_type: 'paired_host_removed_reads', scope: 'sample' }], parameters_schema: {},
      resources: { default_cpus: 4, default_memory_gb: 8 }, database_requirements: ['host.grch38_bowtie2'],
    },
  ],
}

const connected: WorkflowDocument = {
  schema_version: '1.0', accepted_risks: [],
  nodes: [
    { id: 'input', type: 'fastq_input', parameters: {}, position: { x: 20, y: 20 } },
    { id: 'host', type: 'host_depletion', parameters: {}, position: { x: 260, y: 20 } },
  ],
  edges: [{ id: 'e1', source_node: 'input', source_port: 'reads', target_node: 'host', target_port: 'reads' }],
}

describe('WorkflowEditor', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => [] }))
  })

  it('requires second confirmation and persists accepted risk before running', async () => {
    const onRun = vi.fn()
    render(<WorkflowEditor initialRegistry={registry} initialWorkflow={connected} onRun={onRun} />)
    await userEvent.click(screen.getByRole('button', { name: '运行工作流' }))
    expect(screen.getByRole('dialog', { name: '风险确认' })).toBeInTheDocument()
    expect(onRun).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: /我已了解/ }))
    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({
      nodes: expect.arrayContaining([expect.objectContaining({ position: { x: 20, y: 20 }, parameters: {} })]),
      edges: connected.edges,
      accepted_risks: ['risk:host:host_depletion_without_fastp'],
    }))
  })

  it('blocks execution and focuses a node with a hard error', async () => {
    const onRun = vi.fn()
    render(<WorkflowEditor initialRegistry={registry} initialWorkflow={{ ...connected, edges: [] }} onRun={onRun} />)
    await userEvent.click(screen.getByRole('button', { name: '运行工作流' }))
    expect(onRun).not.toHaveBeenCalled()
    expect(screen.getAllByText('去除宿主序列.reads 需要上游连线。')).not.toHaveLength(0)
  })
})
