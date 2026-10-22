nextflow.enable.dsl = 2

params.input_manifest = null
params.outdir = 'results'
params.task_id = 'smoke'

include { VALIDATE_MANIFEST } from './modules/validate'

process PUBLISH_VALIDATED_MANIFEST {
    publishDir "${params.outdir}/validate", mode: 'copy', overwrite: true

    input:
    path validated_manifest

    output:
    path 'manifest.validated.csv'

    script:
    """
    cp '${validated_manifest}' published_manifest.csv
    mv published_manifest.csv manifest.validated.csv
    """
}

workflow {
    if (!params.input_manifest) {
        error('input_manifest is required')
    }

    manifest = Channel.fromPath(params.input_manifest, checkIfExists: true)
    validated = VALIDATE_MANIFEST(manifest)
    PUBLISH_VALIDATED_MANIFEST(validated.validated_manifest)
}
