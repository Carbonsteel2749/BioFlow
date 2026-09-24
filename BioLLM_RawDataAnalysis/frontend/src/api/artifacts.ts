import { ApiError } from './tasks'
import type { Artifact, ArtifactCenterPayload, ArtifactListQuery, ArtifactGenerationNotice } from '../artifact-center/types'

const API_ROOT = import.meta.env.VITE_API_ROOT ?? '/api'

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    let message = `请求失败 (${response.status})`
    try {
      const body = await response.json()
      message = typeof body.detail === 'string' ? body.detail : message
    } catch { /* response is not JSON */ }
    throw new ApiError(response.status, message)
  }
  return response.json() as Promise<T>
}

function queryString(query: ArtifactListQuery = {}) {
  const parameters = new URLSearchParams()
  if (query.artifact_type) parameters.set('artifact_type', query.artifact_type)
  if (query.producer) parameters.set('producer', query.producer)
  if (query.sample_scope) parameters.set('sample_scope', query.sample_scope)
  const value = parameters.toString()
  return value ? `?${value}` : ''
}

export const artifactsApi = {
  list: (taskId: string, query?: ArtifactListQuery) => request<Artifact[]>(
    `/tasks/${encodeURIComponent(taskId)}/artifacts${queryString(query)}`,
  ),
  detail: (artifactId: string) => request<Artifact>(`/artifacts/${encodeURIComponent(artifactId)}`),
  downloadUrl: (artifactId: string) => `${API_ROOT}/artifacts/${encodeURIComponent(artifactId)}/download`,
  contentUrl: (artifactId: string) => `${API_ROOT}/artifacts/${encodeURIComponent(artifactId)}/download`,
}

export async function loadArtifacts(taskId: string): Promise<ArtifactCenterPayload> {
  const [artifacts, notices] = await Promise.all([
    artifactsApi.list(taskId),
    request<ArtifactGenerationNotice[]>(`/tasks/${encodeURIComponent(taskId)}/figure-notices`).catch(error => {
      if (error instanceof ApiError && error.status === 404) return []
      throw error
    }),
  ])
  return { artifacts, notices: Array.isArray(notices) ? notices.filter(item => typeof item.message === 'string' && ['skipped','generation_failed','validation_failed'].includes(item.state)) : [] }
}
