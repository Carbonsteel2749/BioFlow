process BIN_ANNOTATION {
    tag "${cohort_id}"
    cpus params.mag_threads
    publishDir path: { "${params.outdir}/mag/${cohort_id}/bin_annotation" }, mode: 'copy', overwrite: true
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
    """
    mkdir -p annotation
    ${projectDir}/bin/mag/bin_annotation.sh --cohort-id ${cohort_id} --bins ${bins} --outdir annotation --threads ${task.cpus} --database-manifest ${database_manifest} --task-id ${params.task_id} --state-root ${params.outdir}
    """
}
