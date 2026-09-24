import { ApiError } from './tasks'
import type {
  DynamicWorkflowRun,
  NodeRegistryResponse,
  WorkflowDocument,
  WorkflowTemplate,
  WorkflowTemplateSummary,
} from '../workflow-editor/types'

const API_ROOT = import.meta.env.VITE_API_ROOT ?? '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (!response.ok) {
    let message = `请求失败 (${response.status})`
    try {
      const body = await response.json()
      message = typeof body.detail === 'string' ? body.detail : message
    } catch { /* non-JSON error */ }
    throw new ApiError(response.status, message)
  }
  return response.json() as Promise<T>
}

export const workflowsApi = {
  registry: () => request<NodeRegistryResponse>('/workflows/registry'),
  templates: () => request<WorkflowTemplateSummary[]>('/workflows/templates'),
  template: (id: string, version?: number) => request<WorkflowTemplate>(
    `/workflows/templates/${encodeURIComponent(id)}${version ? `?version=${version}` : ''}`,
  ),
  createTemplate: (name: string, description: string, workflow: WorkflowDocument) =>
    request<WorkflowTemplate>('/workflows/templates', {
      method: 'POST',
      body: JSON.stringify({ name, description, workflow }),
    }),
  updateTemplate: (
    id: string,
    baseVersion: number,
    name: string,
    description: string,
    workflow: WorkflowDocument,
  ) => request<WorkflowTemplate>(`/workflows/templates/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify({ base_version: baseVersion, name, description, workflow }),
  }),
  createRun: (manifestPath: string, workflow: WorkflowDocument) =>
    request<DynamicWorkflowRun>('/workflows/runs', {
      method: 'POST',
      body: JSON.stringify({ manifest_path: manifestPath, workflow }),
    }),
  run: (taskId: string) => request<DynamicWorkflowRun>(
    `/workflows/runs/${encodeURIComponent(taskId)}`,
  ),
  runLog: (taskId: string) => request<{ text: string }>(
    `/workflows/runs/${encodeURIComponent(taskId)}/logs`,
  ),
}
