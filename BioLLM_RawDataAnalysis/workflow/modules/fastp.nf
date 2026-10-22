process FASTP {
    tag "${sample_id}"
    cpus params.threads
    publishDir path: { "${params.outdir}/${task.ext.bioflow_output_subdir ? task.ext.bioflow_output_subdir + '/' : ''}fastp/${sample_id}" }, mode: 'copy', overwrite: true
    input:
    tuple val(sample_id), path(read1), path(read2)
    output:
    tuple val(sample_id), path("${sample_id}.R1.clean.fastq.gz"), path("${sample_id}.R2.clean.fastq.gz"), emit: reads
    tuple val(sample_id), path("${sample_id}.fastp.json"), path("${sample_id}.fastp.html"), emit: reports
    script:
    def options = task.ext.bioflow_parameters ?: params
    def stateRoot = task.ext.bioflow_output_subdir ? "${params.outdir}/${task.ext.bioflow_output_subdir}" : params.outdir
    def scriptRoot = task.ext.bioflow_script_root ?: "${projectDir}/bin"
    """
    # bioflow-node ${task.ext.bioflow_fingerprint ?: 'standard'} repair ${task.ext.bioflow_repair_digest ?: 'original'}
    "${scriptRoot}/core/fastp.sh" --sample "${sample_id}" --r1 "${read1}" --r2 "${read2}" \
      --outdir . --threads "${task.cpus}" \
      --qualified-quality-phred "${options.fastp_qualified_quality_phred}" \
      --unqualified-percent-limit "${options.fastp_unqualified_percent_limit}" \
      --n-base-limit "${options.fastp_n_base_limit}" \
      --length-required "${options.fastp_length_required}" \
      --cut-front "${options.fastp_cut_front}" --cut-tail "${options.fastp_cut_tail}" \
      --cut-window-size "${options.fastp_cut_window_size}" \
      --cut-mean-quality "${options.fastp_cut_mean_quality}" \
      --trim-poly-g "${options.fastp_trim_poly_g}" --correction "${options.fastp_correction}" \
      --detect-adapter-for-pe "${options.fastp_detect_adapter_for_pe}" \
      --task-id "${params.task_id}" --state-root "${stateRoot}"
    """
}
