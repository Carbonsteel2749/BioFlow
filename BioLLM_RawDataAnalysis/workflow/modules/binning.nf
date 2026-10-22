process BINNING {
    tag "coassembly"
    cpus params.mag_threads
    publishDir path: { "${params.outdir}/${task.ext.bioflow_output_subdir ? task.ext.bioflow_output_subdir + '/' : ''}mag/binning" }, mode: 'copy', overwrite: true
    input:
    tuple path(assembly), path(reads)
    output:
    tuple path("binning/metabat2_bins"), path("binning/maxbin2_bins"), path("binning/concoct_bins"), path(assembly), path(reads), emit: bins
    script:
    def stateRoot = task.ext.bioflow_output_subdir ? "${params.outdir}/${task.ext.bioflow_output_subdir}" : params.outdir
    def scriptRoot = task.ext.bioflow_script_root ?: "${projectDir}/bin"
    """
    # bioflow-node ${task.ext.bioflow_fingerprint ?: 'standard'} repair ${task.ext.bioflow_repair_digest ?: 'original'}
    printf '%s\n' ${reads.join(' ')} | tr ' ' '\n' > reads.list
    mkdir -p binning
    "${scriptRoot}/mag/binning.sh" --assembly "${assembly}" --reads-list reads.list --outdir binning \
      --threads ${task.cpus} --task-id "${params.task_id}" --state-root "${stateRoot}"
    """
}
