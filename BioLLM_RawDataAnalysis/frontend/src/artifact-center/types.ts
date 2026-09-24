export type ArtifactCategory = 'qc' | 'taxonomy' | 'functional' | 'mag' | 'report'
export type PaperSection = 'Introduction' | 'Method' | 'Result' | 'Discussion'
export type ArtifactScope = 'global' | 'sample' | 'cohort'
export type ArtifactStatus = 'active' | 'missing' | 'validation_failed' | 'generation_failed' | 'skipped'

export interface ArtifactMetadata {
  title?: string
  description?: string
  source?: string
  parameters?: Record<string, unknown>
  paper_sections?: PaperSection[]
  skip_reason?: string
  error_message?: string
  [key: string]: unknown
}

/** Public Artifact API response. Deliberately contains no filesystem path. */
export interface Artifact {
  artifact_id: string
  task_id: string
  node_id: string | null
  producer: string
  artifact_type: string
  schema_version: string
  sample_scope: ArtifactScope
  sample_id: string | null
  cohort_id: string | null
  media_type: string
  file_name: string
  sha256: string
  size_bytes: number
  metadata: ArtifactMetadata
  downloadable: boolean
  status: ArtifactStatus | string
  created_at: string
  derived_from: string[]
  download_url: string | null
}

export type ArtifactGenerationState = 'skipped' | 'generation_failed' | 'validation_failed'

export interface ArtifactGenerationNotice {
  id: string
  category: ArtifactCategory
  artifact_type: string
  node_id: string | null
  sample_id: string | null
  state: ArtifactGenerationState
  message: string
}

export interface ArtifactCenterPayload {
  artifacts: Artifact[]
  notices: ArtifactGenerationNotice[]
}

export interface ArtifactListQuery {
  artifact_type?: string
  producer?: string
  sample_scope?: ArtifactScope
}

export type ArtifactCenterLoader = (taskId: string) => Promise<ArtifactCenterPayload>
