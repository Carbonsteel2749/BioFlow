process BIN_QUANTIFICATION {
    tag "coassembly"
    cpus params.mag_threads
    publishDir path: { "${params.outdir}/${task.ext.bioflow_output_subdir ? task.ext.bioflow_output_subdir + '/' : ''}mag/bin_quantification" }, mode: 'copy', overwrite: true
    input:
    tuple path(bins), path(assembly), path(reads)
    path sample_manifest
    path database_manifest
    output:
    path "quantification/bin_abundance_table.tab", emit: reports
    path "quantification/sample_mag_abundance.tsv", emit: abundance
    path "quantification/sample_mag_abundance.provenance.json", emit: provenance
    script:
    def stateRoot = task.ext.bioflow_output_subdir ? "${params.outdir}/${task.ext.bioflow_output_subdir}" : params.outdir
    def scriptRoot = task.ext.bioflow_script_root ?: "${projectDir}/bin"
    """
    # bioflow-node ${task.ext.bioflow_fingerprint ?: 'standard'} repair ${task.ext.bioflow_repair_digest ?: 'original'}
    printf '%s\n' ${reads.join(' ')} | tr ' ' '\n' > reads.list
    mkdir -p quantification
    "${scriptRoot}/mag/bin_quantification.sh" --bins "${bins}" --assembly "${assembly}" --reads-list reads.list --sample-manifest "${sample_manifest}" --database-manifest "${database_manifest}" --outdir quantification --threads ${task.cpus} --task-id "${params.task_id}" --state-root "${stateRoot}"
    """
}
