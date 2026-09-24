import { ApiError } from './tasks'
import type { Artifact } from '../artifact-center/types'

export interface Page<T> { items:T[]; total:number; limit:number; offset:number }
export interface Dataset { id:string; name:string; stage:string; source:string; status:string; reason:string|null; created_at:string; sample_count:number; size_bytes:number; task_count:number }
export interface DatasetFile { name:string; size_bytes:number; upload_id:string|null; checksum:string|null; validation:string; availability:string }
export interface DatasetDetail extends Dataset {
  samples:Array<{sample_id:string;read1:DatasetFile;read2:DatasetFile}>
  tasks:Array<{id:string;name?:string|null;status:string;created_at:string;kind:string;validation_status:string|null}>
  verification_note:string
  sample_total?:number
  processed_reads?:Array<{artifact_id:string;task_id:string;sample_id:string|null;artifact_type:string;node_id:string|null;producer:string;status:string}>
}
export interface UnpairedUpload {id:string;original_name:string;size_bytes:number;created_at:string}
export interface DatasetReuse {dataset_id:string;manifest_path:string;sample_count:number;name:string}
export interface CatalogResult extends Artifact {task_name?:string|null;task_status:string;task_kind:string;partial:boolean;has_result_archive:boolean;run_parameters:Record<string,unknown>}

const ROOT=import.meta.env.VITE_API_ROOT??'/api'
async function request<T>(path:string,method='GET'):Promise<T> {
  const response=await fetch(`${ROOT}${path}`,{method,headers:{Accept:'application/json'}})
  if(!response.ok) {
    let message=`请求失败 (${response.status})`
    try {const body=await response.json(); if(typeof body.detail==='string')message=body.detail} catch { /* non-JSON response */ }
    throw new ApiError(response.status,message)
  }
  return response.json() as Promise<T>
}
async function paged<T>(path:string,query:Record<string,string|number>):Promise<Page<T>> {
  const params=new URLSearchParams(Object.entries(query).map(([key,value])=>[key,String(value)]))
  const result=await request<Page<T>>(`${path}?${params}`)
  if(!Array.isArray(result.items)||typeof result.total!=='number')throw new Error('目录接口返回格式无效，请刷新重试。')
  return result
}
export const catalogApi={
  datasets:(q='',offset=0)=>paged<Dataset>('/datasets',{q,offset,limit:20}),
  dataset:(id:string,sample_offset=0,task_offset=0,sample_q='')=>request<DatasetDetail>(`/datasets/${encodeURIComponent(id)}?${new URLSearchParams({sample_offset:String(sample_offset),task_offset:String(task_offset),sample_q})}`),
  pending:(offset=0)=>paged<UnpairedUpload>('/datasets-unpaired-uploads',{offset,limit:20}),
  reuse:(id:string)=>request<DatasetReuse>(`/datasets/${encodeURIComponent(id)}/reuse`,'POST'),
  results:(query:Record<string,string|number>)=>paged<CatalogResult>('/results',{limit:20,...query}),
  resultTypes:()=>request<string[]>('/result-types'),
  refreshResults:(offset=0)=>request<{processed:number;next_offset:number;remaining:number;warnings:Array<{task_id:string;message:string}>}>(`/results/refresh?offset=${offset}`,'POST'),
}
