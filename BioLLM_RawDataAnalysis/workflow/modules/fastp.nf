process FASTP {
    tag "${sample_id}"
    cpus params.threads
    publishDir path: { "${params.outdir}/fastp/${sample_id}" }, mode: 'copy', overwrite: true
    input:
    tuple val(sample_id), path(read1), path(read2)
    output:
    tuple val(sample_id), path("${sample_id}.R1.clean.fastq.gz"), path("${sample_id}.R2.clean.fastq.gz"), emit: reads
    tuple val(sample_id), path("${sample_id}.fastp.json"), path("${sample_id}.fastp.html"), emit: reports
    script:
    """
    "${projectDir}/bin/core/fastp.sh" --sample "${sample_id}" --r1 "${read1}" --r2 "${read2}" \
      --outdir . --threads "${task.cpus}" \
      --qualified-quality-phred "${params.fastp_qualified_quality_phred}" \
      --unqualified-percent-limit "${params.fastp_unqualified_percent_limit}" \
      --n-base-limit "${params.fastp_n_base_limit}" \
      --length-required "${params.fastp_length_required}" \
      --cut-front "${params.fastp_cut_front}" --cut-tail "${params.fastp_cut_tail}" \
      --cut-window-size "${params.fastp_cut_window_size}" \
      --cut-mean-quality "${params.fastp_cut_mean_quality}" \
      --trim-poly-g "${params.fastp_trim_poly_g}" --correction "${params.fastp_correction}" \
      --detect-adapter-for-pe "${params.fastp_detect_adapter_for_pe}" \
      --task-id "${params.task_id}" --state-root "${params.outdir}"
    """
}
