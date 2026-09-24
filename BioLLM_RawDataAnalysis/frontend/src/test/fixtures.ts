import type { AnalysisTask, StepName, StepStatus, WorkflowParameters } from '../types'

export const parameters: WorkflowParameters = {
  threads: 4,
  fastp_qualified_quality_phred: 20,
  fastp_unqualified_percent_limit: 40,
  fastp_n_base_limit: 5,
  fastp_length_required: 50,
  fastp_cut_front: true,
  fastp_cut_tail: true,
  fastp_cut_window_size: 4,
  fastp_cut_mean_quality: 20,
  fastp_trim_poly_g: true,
  fastp_correction: false,
  fastp_detect_adapter_for_pe: true,
  enable_mags: false,
  enable_reassembly: true,
  mag_threads: 8,
  mag_memory_gb: 32,
  assembler: 'megahit',
  bin_completeness: 70,
  bin_contamination: 5,
  host_index: '/srv/reference/GRCh38',
  host_filter_mode: 'strict_both_unmapped',
  host_min_retained_pairs: 0,
  host_max_removed_pct: 100,
  host_bowtie2_preset: 'very-sensitive',
  kraken_db: '/srv/databases/kraken',
  read_length: 150,
  humann_nucleotide_db: '/srv/databases/chocophlan',
  humann_protein_db: '/srv/databases/uniref',
  metaphlan_db: '/srv/databases/metaphlan',
}

const coreSteps: StepName[] = ['validate','fastqc_raw','fastp','host_depletion','taxonomy','functional_annotation','report']

export function makeTask(overrides: Partial<AnalysisTask> = {}): AnalysisTask {
  return {
    id: '12345678-1234-5678-1234-567812345678',
    manifest_path: '/srv/incoming/demo.csv',
    status: 'running',
    created_at: '2026-07-27T01:00:00Z',
    updated_at: '2026-07-27T01:02:00Z',
    started_at: '2026-07-27T01:00:10Z',
    finished_at: null,
    current_step: 'fastp',
    retry_allowed: false,
    retry_count: 0,
    error_message: null,
    result_archive: null,
    parameters: { ...parameters },
    steps: coreSteps.map((name, index) => step(name, index < 2 ? 'succeeded' : index === 2 ? 'running' : 'pending')),
    alerts: [],
    ...overrides,
  }
}

export function step(name: StepName, status: StepStatus = 'pending') {
  return {
    name,
    status,
    started_at: status === 'pending' ? null : '2026-07-27T01:00:10Z',
    finished_at: status === 'succeeded' || status === 'failed' ? '2026-07-27T01:01:00Z' : null,
    progress: status === 'succeeded' ? 100 : status === 'running' ? 40 : null,
    error_message: status === 'failed' ? 'Bowtie2 exited with status 1' : null,
  }
}
