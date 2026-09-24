process BIN_REFINEMENT {
    tag "coassembly"
    cpus params.mag_threads
    publishDir "${params.outdir}/mag/bin_refinement", mode: 'copy', overwrite: true
    input:
    tuple path(bins_a), path(bins_b), path(bins_c), path(assembly), path(reads)
    output:
    tuple path("refinement/metawrap_${params.bin_completeness}_${params.bin_contamination}_bins"), path(assembly), path(reads), emit: refined
    path "refinement/*.stats", emit: reports
    script:
    """
    mkdir -p refinement
    ${projectDir}/bin/mag/bin_refinement.sh --bins-a ${bins_a} --bins-b ${bins_b} --bins-c ${bins_c} \
      --outdir refinement --threads ${task.cpus} --completeness ${params.bin_completeness} \
      --contamination ${params.bin_contamination} --task-id ${params.task_id} --state-root ${params.outdir}
    """
}
