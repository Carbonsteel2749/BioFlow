nextflow.enable.dsl = 2

params.input_manifest = null
params.outdir = 'results'
params.task_id = 'smoke'
params.threads = 1
params.fastp_qualified_quality_phred = 20
params.fastp_unqualified_percent_limit = 40
params.fastp_n_base_limit = 5
params.fastp_length_required = 50
params.fastp_cut_front = true
params.fastp_cut_tail = true
params.fastp_cut_window_size = 4
params.fastp_cut_mean_quality = 20
params.fastp_trim_poly_g = true
params.fastp_correction = false
params.fastp_detect_adapter_for_pe = true

include { FASTP } from './modules/fastp'

workflow {
    if (!params.input_manifest) {
        error('input_manifest is required')
    }

    samples = Channel.fromPath(params.input_manifest, checkIfExists: true)
        .splitCsv(header: true)
        .map { row -> tuple(row.sample_id, file(row.read1), file(row.read2)) }

    FASTP(samples)
}
