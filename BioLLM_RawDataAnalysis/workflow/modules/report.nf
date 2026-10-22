process REPORT {
    tag "${params.task_id}"
    publishDir path: { task.ext.bioflow_output_subdir ? "${params.outdir}/${task.ext.bioflow_output_subdir}" : params.outdir }, mode: 'copy', overwrite: true
    input:
    path report_files, stageAs: "inputs/*/*"
    path database_manifest, name: "report-database.resolved.json"
    val input_manifest_path
    val parameters_base64
    val run_name
    val nextflow_work_root
    output:
    path "report/multiqc_report.html", emit: multiqc
    path "report/multiqc_report_data", optional: true, emit: multiqc_data
    path "report/summary.txt", emit: summary
    path "report/summary.json", emit: summary_json
    path "report/README.txt", emit: report_entry
    path "tables", emit: tables
    path "provenance", emit: provenance
    path "deliverables/${params.task_id}.tar.gz", emit: archive
    script:
    def stateRoot = task.ext.bioflow_output_subdir ? "${params.outdir}/${task.ext.bioflow_output_subdir}" : params.outdir
    def scriptRoot = task.ext.bioflow_script_root ?: "${projectDir}/bin"
    """
    # bioflow-node ${task.ext.bioflow_fingerprint ?: 'standard'} repair ${task.ext.bioflow_repair_digest ?: 'original'}
    "${scriptRoot}/core/report.sh" \
      --results-root . \
      --outdir report \
      --task-id "${params.task_id}" \
      --state-root "${stateRoot}" \
      --nextflow-work-root "${nextflow_work_root}" \
      --database-manifest "${database_manifest}" \
      --input-manifest "${input_manifest_path}" \
      --parameters-base64 "${parameters_base64}" \
      --run-name "${run_name}" \
      --project-root "${projectDir}/.."
    """
}
