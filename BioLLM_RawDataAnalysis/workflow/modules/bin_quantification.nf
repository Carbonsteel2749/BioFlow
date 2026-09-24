process BIN_QUANTIFICATION {
    tag "coassembly"
    cpus params.mag_threads
    publishDir "${params.outdir}/mag/bin_quantification", mode: 'copy', overwrite: true
    input:
    tuple path(bins), path(assembly), path(reads)
    path sample_manifest
    path database_manifest
    output:
    path "quantification/bin_abundance_table.tab", emit: reports
    path "quantification/sample_mag_abundance.tsv", emit: abundance
    path "quantification/sample_mag_abundance.provenance.json", emit: provenance
    script:
    """
    printf '%s\n' ${reads.join(' ')} | tr ' ' '\n' > reads.list
    mkdir -p quantification
    ${projectDir}/bin/mag/bin_quantification.sh --bins ${bins} --assembly ${assembly} --reads-list reads.list --sample-manifest ${sample_manifest} --database-manifest ${database_manifest} --outdir quantification --threads ${task.cpus} --task-id ${params.task_id} --state-root ${params.outdir}
    """
}
