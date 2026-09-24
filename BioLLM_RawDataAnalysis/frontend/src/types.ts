export type TaskStatus = 'queued' | 'validating' | 'running' | 'paused' | 'failed' | 'completed' | 'cancelled'
export type StepStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped'

export type StepName =
  | 'validate' | 'fastqc_raw' | 'fastp' | 'host_depletion' | 'taxonomy' | 'functional_annotation'
  | 'assembly' | 'binning' | 'bin_refinement' | 'bin_quantification' | 'bin_reassembly' | 'bin_annotation'
  | 'report'

export interface WorkflowStep {
  name: StepName
  status: StepStatus
  started_at: string | null
  finished_at: string | null
  progress: number | null
  error_message: string | null
}

export interface Alert {
  level: 'info' | 'warning' | 'error' | string
  message: string
  created_at: string
}

export interface WorkflowParameters {
  threads: number
  fastp_qualified_quality_phred: number
  fastp_unqualified_percent_limit: number
  fastp_n_base_limit: number
  fastp_length_required: number
  fastp_cut_front: boolean
  fastp_cut_tail: boolean
  fastp_cut_window_size: number
  fastp_cut_mean_quality: number
  fastp_trim_poly_g: boolean
  fastp_correction: boolean
  fastp_detect_adapter_for_pe: boolean
  enable_mags: boolean
  enable_reassembly: boolean
  mag_threads: number
  mag_memory_gb: number
  assembler: 'megahit' | 'metaspades'
  bin_completeness: number
  bin_contamination: number
  host_index: string | null
  host_filter_mode: 'strict_both_unmapped' | 'concordant_unmapped'
  host_min_retained_pairs: number
  host_max_removed_pct: number
  host_bowtie2_preset: 'very-fast' | 'fast' | 'sensitive' | 'very-sensitive'
  kraken_db: string | null
  read_length: 50 | 75 | 100 | 150 | 200 | 250 | 300
  humann_nucleotide_db: string | null
  humann_protein_db: string | null
  metaphlan_db: string | null
}

export interface AnalysisTask {
  id: string
  name?: string | null
  manifest_path: string
  status: TaskStatus
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
  current_step: StepName | null
  retry_allowed: boolean
  retry_count: number
  error_message: string | null
  result_archive: string | null
  parameters: WorkflowParameters
  steps: WorkflowStep[]
  alerts: Alert[]
}

export type CreateTaskPayload = { manifest_path: string; parameters: Partial<WorkflowParameters>; dataset_id?:string; name?:string }

export interface DatasetCleanupPlan {dataset_id:string|null;name:string|null;will_delete:boolean;reasons:string[];scope:string[];estimated_bytes:number}
export interface CleanupPreview {token:string;estimated_bytes:number;scope:string[];dataset_cleanup?:DatasetCleanupPlan|null}

export interface PlatformCapabilities {
  reads_analysis: boolean
  mag_analysis: boolean
  mag_unavailable_reason: string | null
  database_profile: string
  file_uploads?: boolean
  task_cancellation?: boolean
  result_preview?: boolean
}

export interface UploadedFile {
  id: string
  original_name: string
  size: number
  checksum?: string
  status: 'uploaded' | 'validated'
}

export interface UploadedManifest {
  dataset_id?: string
  manifest_path: string
  sample_count: number
}

export interface QcPreviewRow {
  sample_id: string
  raw_reads: number
  clean_reads: number
  host_removed_pct: number | null
}

export interface AbundancePreviewRow {
  sample_id?: string
  name: string
  abundance: number
}

export interface TablePreview {
  measurement?: ResultMeasurement
  columns: string[]
  rows: Array<Array<string | number | null>>
  total_rows?: number
  truncated?: boolean
}

export interface ResultMeasurement {
  status: 'known' | 'unknown' | 'conflict'
  source_unit: string
  display_unit: string
  normalization_method: string
  conversion_factor: number
  basis: string
  note: string
}

export interface ResultPreview {
  sample_ids?: string[]
  selected_sample_id?: string
  taxonomy_truncated?: boolean
  taxonomy_measurement?: ResultMeasurement
  multiqc_url: string | null
  qc_summary: QcPreviewRow[]
  taxonomy_top: AbundancePreviewRow[]
  ko: TablePreview | null
  ec: TablePreview | null
  pathways: TablePreview | null
}
