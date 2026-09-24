process HOST_DEPLETION {
    tag "${sample_id}"
    cpus params.threads
    publishDir path: { "${params.outdir}/host_depletion/${sample_id}" }, mode: 'copy', overwrite: true
    input:
    tuple val(sample_id), path(read1), path(read2)
    output:
    tuple val(sample_id), path("${sample_id}.R1.host_removed.fastq.gz"), path("${sample_id}.R2.host_removed.fastq.gz"), emit: reads
    tuple val(sample_id), path("${sample_id}.host_depletion.metrics.json"), emit: metrics
    script:
    """
    "${projectDir}/bin/core/host_depletion.sh" --sample "${sample_id}" --r1 "${read1}" --r2 "${read2}" \
      --host-index "${params.host_index}" --outdir . --threads "${task.cpus}" \
      --filter-mode "${params.host_filter_mode}" \
      --min-retained-pairs "${params.host_min_retained_pairs}" \
      --max-removed-pct "${params.host_max_removed_pct}" \
      --bowtie2-preset "${params.host_bowtie2_preset}" \
      --task-id "${params.task_id}" --state-root "${params.outdir}"
    """
}
