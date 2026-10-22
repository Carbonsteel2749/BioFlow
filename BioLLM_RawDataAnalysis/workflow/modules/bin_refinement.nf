process BIN_REFINEMENT {
    tag "coassembly"
    cpus params.mag_threads
    publishDir path: { "${params.outdir}/${task.ext.bioflow_output_subdir ? task.ext.bioflow_output_subdir + '/' : ''}mag/bin_refinement" }, mode: 'copy', overwrite: true
    input:
    tuple path(bins_a), path(bins_b), path(bins_c), path(assembly), path(reads)
    output:
    tuple path("refinement/metawrap_${task.ext.bioflow_parameters ? task.ext.bioflow_parameters.bin_completeness : params.bin_completeness}_${task.ext.bioflow_parameters ? task.ext.bioflow_parameters.bin_contamination : params.bin_contamination}_bins"), path(assembly), path(reads), emit: refined
    path "refinement/*.stats", emit: reports
    script:
    def options = task.ext.bioflow_parameters ?: params
    def stateRoot = task.ext.bioflow_output_subdir ? "${params.outdir}/${task.ext.bioflow_output_subdir}" : params.outdir
    def scriptRoot = task.ext.bioflow_script_root ?: "${projectDir}/bin"
    """
    # bioflow-node ${task.ext.bioflow_fingerprint ?: 'standard'} repair ${task.ext.bioflow_repair_digest ?: 'original'}
    mkdir -p refinement
    "${scriptRoot}/mag/bin_refinement.sh" --bins-a "${bins_a}" --bins-b "${bins_b}" --bins-c "${bins_c}" \
      --outdir refinement --threads ${task.cpus} --completeness ${options.bin_completeness} \
      --contamination ${options.bin_contamination} --task-id "${params.task_id}" --state-root "${stateRoot}"
    """
}
