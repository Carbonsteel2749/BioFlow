export type JsonPrimitive = string | number | boolean | null

export interface CanvasPosition {
  x: number
  y: number
}

export interface WorkflowNode {
  id: string
  type: string
  parameters: Record<string, JsonPrimitive>
  position?: CanvasPosition | null
}

export interface WorkflowEdge {
  id: string
  source_node: string
  source_port: string
  target_node: string
  target_port: string
}

export interface WorkflowDocument {
  schema_version: '1.0'
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  accepted_risks: string[]
}

export interface InputPortDefinition {
  id: string
  accepted_data_types: string[]
  accepted_scopes: string[]
  required: boolean
  multiple: boolean
  collect: boolean
}

export interface OutputPortDefinition {
  id: string
  data_type: string
  scope: string
}

export interface ParameterSchema {
  type: 'integer' | 'number' | 'string' | 'boolean'
  default?: JsonPrimitive
  minimum?: number
  maximum?: number
  enum?: JsonPrimitive[]
  title?: string
  description?: string
}

export interface NodeDefinition {
  type: string
  version: string
  label: string
  category: string
  inputs: InputPortDefinition[]
  outputs: OutputPortDefinition[]
  parameters_schema: Record<string, ParameterSchema>
  resources: {
    default_cpus: number
    default_memory_gb: number
  }
  database_requirements: string[]
}

export interface NodeRegistryResponse {
  registry_version: string
  nodes: NodeDefinition[]
}

export interface WorkflowValidationIssue {
  severity: 'hard_error' | 'warning'
  code: string
  message: string
  node_ids: string[]
  edge_id?: string | null
  confirmation_key?: string | null
  confirmed?: boolean
}

export interface WorkflowValidationResult {
  structurally_valid: boolean
  can_execute: boolean
  topological_order: string[]
  issues: WorkflowValidationIssue[]
}

export interface WorkflowTemplateSummary {
  template_id: string
  version: number
  name: string
  description: string
  source: 'builtin' | 'user' | string
  read_only: boolean
}

export interface WorkflowTemplate extends WorkflowTemplateSummary {
  workflow: WorkflowDocument
  validation?: WorkflowValidationResult
}

export type WorkflowNodeRunStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'skipped'
  | 'paused'
  | 'cancelled'

export interface WorkflowNodeRuntime {
  status: WorkflowNodeRunStatus
  progress?: number | null
  log_url?: string | null
  result_url?: string | null
}

export interface DynamicWorkflowNodeRun extends WorkflowNodeRuntime {
  node_id: string
  ordinal: number
  node_type: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
}

export interface DynamicWorkflowRun {
  task_id: string
  graph_hash: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'paused' | 'cancelled'
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  input_manifest: string
  nodes: DynamicWorkflowNodeRun[]
}
