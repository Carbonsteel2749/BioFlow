nextflow.enable.dsl = 2

params.input_manifest = null
params.outdir = 'results'
params.task_id = 'smoke'
params.threads = 1
params.host_index = ''
params.host_filter_mode = 'strict_both_unmapped'
params.host_min_retained_pairs = 0
params.host_max_removed_pct = 100
params.host_bowtie2_preset = 'very-sensitive'

include { HOST_DEPLETION } from './modules/host_depletion'

workflow {
    if (!params.input_manifest) {
        error('input_manifest is required')
    }

    samples = Channel.fromPath(params.input_manifest, checkIfExists: true)
        .splitCsv(header: true)
        .map { row -> tuple(row.sample_id, file(row.read1), file(row.read2)) }

    HOST_DEPLETION(samples)
}
