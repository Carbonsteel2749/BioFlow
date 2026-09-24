import type { AnalysisTask, CreateTaskPayload, PlatformCapabilities, ResultPreview, StepName, UploadedFile, UploadedManifest } from '../types'

const API_ROOT = import.meta.env.VITE_API_ROOT ?? '/api'

export interface InterpretationReasoning {finding:string;interpretation:string;limitation:string;recommendation:string}
export interface InterpretationSection {category:string;sample_id:string;text:string;source_ids:string[];paragraphs?:Array<{title:string;text:string}>;common_notes?:string;diagnostics?:string;detail_text?:string;reasoning?:InterpretationReasoning}
export interface InterpretationHighlights {status:string;message:string;model:string;prompt_version:string;evidence_sha256:string;highlights:Array<InterpretationReasoning & {id:string;sample_id:string;category:string;source_ids:string[]}>}
export interface Interpretation {text:string;partial:boolean;sections:InterpretationSection[];display_sections?:InterpretationSection[];sources:Array<{id:string;path:string;sha256:string}>;warnings:string[];limitations:string[]}
export interface LogFile {id:string;name:string;relative_path:string;step:string;size_bytes:number;modified_at:string;selectable:boolean;reason:string;deleted:boolean}
export interface LogCleanupPreview {token:string;estimated_bytes:number;files:LogFile[]}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

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
    } catch { /* response is not JSON */ }
    throw new ApiError(response.status, message)
  }
  return response.json() as Promise<T>
}

export function uploadFastq(file: File, onProgress: (progress: number) => void): Promise<UploadedFile> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_ROOT}/uploads/files`)
    xhr.responseType = 'json'
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded * 100 / event.total))
    }
    xhr.onerror = () => reject(new ApiError(0, '网络连接中断，文件上传失败'))
    xhr.onabort = () => reject(new ApiError(0, '文件上传已取消'))
    xhr.onload = () => {
      const body = xhr.response
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress(100)
        resolve(body as UploadedFile)
      } else {
        reject(new ApiError(xhr.status, typeof body?.detail === 'string' ? body.detail : `文件上传失败 (${xhr.status})`))
      }
    }
    const data = new FormData()
    data.append('file', file, file.name)
    xhr.send(data)
  })
}

export const tasksApi = {
  rename: (id:string,name:string)=>request<AnalysisTask>(`/tasks/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({name})}),
  datasetCleanupPreview: (id:string)=>request<import('../types').CleanupPreview>(`/datasets/${encodeURIComponent(id)}/cleanup-preview`),
  datasetCleanup: (id:string,token:string)=>request<{status:string}>(`/datasets/${encodeURIComponent(id)}/cleanup`,{method:'POST',body:JSON.stringify({token,confirmation:id})}),
  interpretationPrompt: (id:string)=>request<{text:string}>(`/tasks/${encodeURIComponent(id)}/interpretation/prompt`),
  interpretationRefine: (id:string)=>request<InterpretationHighlights>(`/tasks/${encodeURIComponent(id)}/interpretation/refine`,{method:'POST'}),
  interpretation: (id:string)=>request<Interpretation>(`/tasks/${encodeURIComponent(id)}/interpretation`),
  interpretationDownloadUrl: (id:string)=>`${API_ROOT}/tasks/${encodeURIComponent(id)}/interpretation/download`,
  logFiles: (id:string)=>request<{files:LogFile[];scope:string}>(`/tasks/${encodeURIComponent(id)}/log-files`),
  logFile: (id:string,fileId:string)=>request<{text:string}>(`/tasks/${encodeURIComponent(id)}/log-files/${encodeURIComponent(fileId)}`),
  logCleanupPreview: (id:string,ids:string[])=>request<LogCleanupPreview>(`/tasks/${encodeURIComponent(id)}/log-files/cleanup-preview`,{method:'POST',body:JSON.stringify({ids})}),
  logCleanup: (id:string,ids:string[],token:string)=>request<{removed_count:number;removed_bytes:number}>(`/tasks/${encodeURIComponent(id)}/log-files/cleanup`,{method:'POST',body:JSON.stringify({ids,token,confirmation:id})}),
  cleanupPreview: (id: string, mode: 'cache' | 'delete', deleteDataset=false) => request<import('../types').CleanupPreview>(`/tasks/${encodeURIComponent(id)}/cleanup-preview?mode=${mode}&delete_dataset=${deleteDataset}`),
  cleanup: (id: string, mode: 'cache' | 'delete', token: string, deleteDataset=false) => request<{ status: string; removed_bytes: number;dataset_cleanup?:import('../types').DatasetCleanupPlan|null }>(`/tasks/${encodeURIComponent(id)}/cleanup`, { method: 'POST', body: JSON.stringify({ mode, token, confirmation: id, ...(deleteDataset?{delete_dataset:true}:{}) }) }),
  capabilities: () => request<PlatformCapabilities>('/capabilities'),
  list: () => request<AnalysisTask[]>('/tasks'),
  get: (id: string) => request<AnalysisTask>(`/tasks/${encodeURIComponent(id)}`),
  create: (payload: CreateTaskPayload) => request<AnalysisTask>('/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  log: (id: string, step: StepName) => request<{ step: string; text: string }>(`/tasks/${encodeURIComponent(id)}/logs?step=${encodeURIComponent(step)}`),
  retry: (id: string) => request<AnalysisTask>(`/tasks/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
  retryDecision: (id: string, allowed: boolean, reason: string) => request<AnalysisTask>(`/tasks/${encodeURIComponent(id)}/retry-decision`, { method: 'POST', body: JSON.stringify({ allowed, reason }) }),
  cancel: (id: string) => request<AnalysisTask>(`/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  rerun: (task: AnalysisTask) => request<AnalysisTask>('/tasks', { method: 'POST', body: JSON.stringify({ manifest_path: task.manifest_path, parameters: task.parameters }) }),
  createUploadedManifest: (files: Array<{ sample_id: string; read1_upload_id: string; read2_upload_id: string }>, dataset_name?:string) => request<UploadedManifest>('/uploads/manifests', { method: 'POST', body: JSON.stringify({ files, dataset_name }) }),
  preview: (id: string, sampleId?: string) => request<ResultPreview>(`/tasks/${encodeURIComponent(id)}/preview${sampleId ? `?sample_id=${encodeURIComponent(sampleId)}` : ''}`),
  resultUrl: (id: string) => `${API_ROOT}/tasks/${encodeURIComponent(id)}/results`,
  multiqcUrl: (id: string) => `${API_ROOT}/tasks/${encodeURIComponent(id)}/reports/multiqc`,
}
