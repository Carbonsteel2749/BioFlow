nextflow.enable.dsl = 2

params.input_manifest = null
params.outdir = 'results'
params.task_id = 'smoke'
params.threads = 1

include { FASTQC_RAW } from './modules/fastqc'

workflow {
    if (!params.input_manifest) {
        error('input_manifest is required')
    }

    samples = Channel.fromPath(params.input_manifest, checkIfExists: true)
        .splitCsv(header: true)
        .map { row -> tuple(row.sample_id, file(row.read1), file(row.read2)) }

    FASTQC_RAW(samples)
}
