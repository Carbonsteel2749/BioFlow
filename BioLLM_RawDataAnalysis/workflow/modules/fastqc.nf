process FASTQC_RAW {
    tag "${sample_id}"
    cpus params.threads
    publishDir path: { "${params.outdir}/fastqc/${sample_id}" }, mode: 'copy', overwrite: true
    input:
    tuple val(sample_id), path(read1), path(read2)
    output:
    tuple val(sample_id), path("*_fastqc.html"), path("*_fastqc.zip"), emit: reports
    script:
    """
    "${projectDir}/bin/core/fastqc.sh" --sample "${sample_id}" --r1 "${read1}" --r2 "${read2}" \
      --outdir . --threads "${task.cpus}" --task-id "${params.task_id}" --state-root "${params.outdir}"
    """
}
