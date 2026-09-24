process BINNING {
    tag "coassembly"
    cpus params.mag_threads
    publishDir "${params.outdir}/mag/binning", mode: 'copy', overwrite: true
    input:
    tuple path(assembly), path(reads)
    output:
    tuple path("binning/metabat2_bins"), path("binning/maxbin2_bins"), path("binning/concoct_bins"), path(assembly), path(reads), emit: bins
    script:
    """
    printf '%s\n' ${reads.join(' ')} | tr ' ' '\n' > reads.list
    mkdir -p binning
    ${projectDir}/bin/mag/binning.sh --assembly ${assembly} --reads-list reads.list --outdir binning \
      --threads ${task.cpus} --task-id ${params.task_id} --state-root ${params.outdir}
    """
}
