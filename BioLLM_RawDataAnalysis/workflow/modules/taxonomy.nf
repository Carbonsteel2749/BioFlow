process TAXONOMY {
    tag "${sample_id}"
    cpus params.threads
    publishDir path: { "${params.outdir}/taxonomy/${sample_id}" }, mode: 'copy', overwrite: true
    input:
    tuple val(sample_id), path(read1), path(read2)
    path database_manifest
    output:
    // Compatibility bundle for the fixed workflow; dynamic callers use the typed emits below.
    tuple val(sample_id), path("kraken.report"), path("kraken.output"), path("bracken.species.tsv"), path("species_abundance.tsv"), path("taxonomy.validation.json"), path("taxonomy.provenance.json"), emit: reports
    tuple val(sample_id), path("species_abundance.tsv"), emit: species_abundance
    tuple val(sample_id), path("bracken.species.tsv"), emit: bracken_species
    tuple val(sample_id), path("kraken.report"), path("kraken.output"), emit: kraken_artifacts
    tuple val(sample_id), path("taxonomy.validation.json"), emit: validation
    tuple val(sample_id), path("taxonomy.provenance.json"), emit: provenance
    script:
    """
    ${projectDir}/bin/core/taxonomy.sh --sample ${sample_id} --r1 ${read1} --r2 ${read2} --database-manifest ${database_manifest} --read-length ${params.read_length} --outdir . --threads ${task.cpus} --task-id ${params.task_id} --state-root ${params.outdir}
    """
}
