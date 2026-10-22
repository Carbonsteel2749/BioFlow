process BIN_ANNOTATION {
    tag "${cohort_id}"
    cpus params.mag_threads
    publishDir path: { "${params.outdir}/${task.ext.bioflow_output_subdir ? task.ext.bioflow_output_subdir + '/' : ''}mag/${cohort_id}/bin_annotation" }, mode: 'copy', overwrite: true
    input:
    // MAGs are cohort/coassembly entities; sample_id is intentionally not accepted here.
    tuple val(cohort_id), path(bins)
    path database_manifest
    output:
    tuple val(cohort_id), path("annotation/classification/bin_taxonomy.tab"), emit: raw_taxonomy
    tuple val(cohort_id), path("annotation/function/bin_funct_annotations"), emit: raw_functions
    tuple val(cohort_id), path("annotation/mag_annotation.tsv"), emit: mag_taxonomy
    tuple val(cohort_id), path("annotation/mag_function_annotation.tsv"), emit: mag_functions
    tuple val(cohort_id), path("annotation/mag_annotation.provenance.json"), emit: provenance
    script:
    def stateRoot = task.ext.bioflow_output_subdir ? "${params.outdir}/${task.ext.bioflow_output_subdir}" : params.outdir
    def scriptRoot = task.ext.bioflow_script_root ?: "${projectDir}/bin"
    """
    # bioflow-node ${task.ext.bioflow_fingerprint ?: 'standard'} repair ${task.ext.bioflow_repair_digest ?: 'original'}
    mkdir -p annotation
    "${scriptRoot}/mag/bin_annotation.sh" --cohort-id "${cohort_id}" --bins "${bins}" --outdir annotation --threads ${task.cpus} --database-manifest "${database_manifest}" --task-id "${params.task_id}" --state-root "${stateRoot}"
    """
}
