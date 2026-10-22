process HOST_DEPLETION {
    tag "${sample_id}"
    cpus params.threads
    publishDir path: { "${params.outdir}/${task.ext.bioflow_output_subdir ? task.ext.bioflow_output_subdir + '/' : ''}host_depletion/${sample_id}" }, mode: 'copy', overwrite: true
    input:
    tuple val(sample_id), path(read1), path(read2)
    output:
    tuple val(sample_id), path("${sample_id}.R1.host_removed.fastq.gz"), path("${sample_id}.R2.host_removed.fastq.gz"), emit: reads
    tuple val(sample_id), path("${sample_id}.host_depletion.metrics.json"), emit: metrics
    script:
    def options = task.ext.bioflow_parameters ?: params
    def stateRoot = task.ext.bioflow_output_subdir ? "${params.outdir}/${task.ext.bioflow_output_subdir}" : params.outdir
    def scriptRoot = task.ext.bioflow_script_root ?: "${projectDir}/bin"
    """
    # bioflow-node ${task.ext.bioflow_fingerprint ?: 'standard'} repair ${task.ext.bioflow_repair_digest ?: 'original'}
    "${scriptRoot}/core/host_depletion.sh" --sample "${sample_id}" --r1 "${read1}" --r2 "${read2}" \
      --host-index "${params.host_index}" --outdir . --threads "${task.cpus}" \
      --filter-mode "${options.host_filter_mode}" \
      --min-retained-pairs "${options.host_min_retained_pairs}" \
      --max-removed-pct "${options.host_max_removed_pct}" \
      --bowtie2-preset "${options.host_bowtie2_preset}" \
      --task-id "${params.task_id}" --state-root "${stateRoot}"
    """
}
