process FASTQC_RAW {
    tag "${sample_id}"
    cpus params.threads
    publishDir path: { "${params.outdir}/${task.ext.bioflow_output_subdir ? task.ext.bioflow_output_subdir + '/' : ''}fastqc/${sample_id}" }, mode: 'copy', overwrite: true
    input:
    tuple val(sample_id), path(read1), path(read2)
    output:
    tuple val(sample_id), path("*_fastqc.html"), path("*_fastqc.zip"), emit: reports
    script:
    def stateRoot = task.ext.bioflow_output_subdir ? "${params.outdir}/${task.ext.bioflow_output_subdir}" : params.outdir
    def scriptRoot = task.ext.bioflow_script_root ?: "${projectDir}/bin"
    """
    # bioflow-node ${task.ext.bioflow_fingerprint ?: 'standard'} repair ${task.ext.bioflow_repair_digest ?: 'original'}
    "${scriptRoot}/core/fastqc.sh" --sample "${sample_id}" --r1 "${read1}" --r2 "${read2}" \
      --outdir . --threads "${task.cpus}" --task-id "${params.task_id}" --state-root "${stateRoot}"
    """
}
