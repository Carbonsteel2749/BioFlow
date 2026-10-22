process BIN_REASSEMBLY {
    tag "coassembly"
    cpus params.mag_threads
    publishDir path: { "${params.outdir}/${task.ext.bioflow_output_subdir ? task.ext.bioflow_output_subdir + '/' : ''}mag/bin_reassembly" }, mode: 'copy', overwrite: true
    input:
    tuple path(bins), path(assembly), path(reads)
    output:
    tuple path("reassembly/reassembled_bins"), path(assembly), path(reads), emit: reassembled
    path "reassembly/reassembled_bins.stats", emit: reports
    script:
    def options = task.ext.bioflow_parameters ?: params
    def stateRoot = task.ext.bioflow_output_subdir ? "${params.outdir}/${task.ext.bioflow_output_subdir}" : params.outdir
    def scriptRoot = task.ext.bioflow_script_root ?: "${projectDir}/bin"
    def r1Files = reads.findAll { it.name.contains('.R1.') }
    def r2Files = reads.findAll { it.name.contains('.R2.') }
    """
    # bioflow-node ${task.ext.bioflow_fingerprint ?: 'standard'} repair ${task.ext.bioflow_repair_digest ?: 'original'}
    printf '%s\n' ${r1Files.join(' ')} | tr ' ' '\n' > r1.list
    printf '%s\n' ${r2Files.join(' ')} | tr ' ' '\n' > r2.list
    mkdir -p reassembly
    "${scriptRoot}/mag/bin_reassembly.sh" --bins "${bins}" --r1-list r1.list --r2-list r2.list \
      --outdir reassembly --threads ${task.cpus} --memory-gb ${options.mag_memory_gb} \
      --completeness ${options.bin_completeness} --contamination ${options.bin_contamination} \
      --task-id "${params.task_id}" --state-root "${stateRoot}"
    """
}
