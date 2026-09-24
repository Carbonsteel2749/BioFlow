process FUNCTIONAL_ANNOTATION {
    tag "${sample_id}"
    cpus params.threads
    publishDir path: { "${params.outdir}/functional_annotation/${sample_id}" }, mode: 'copy', overwrite: true
    input:
    tuple val(sample_id), path(read1), path(read2)
    path database_manifest
    output:
    // Compatibility bundle for the fixed workflow; every dynamic-node data emit retains sample_id.
    tuple val(sample_id), path("read_genefamilies.tsv"), path("read_pathabundance.tsv"), path("read_pathways.tsv"), path("read_pathcoverage.tsv"), path("read_ko.tsv"), path("read_ec.tsv"), path("functional.validation.json"), path("humann_raw_genefamilies.tsv"), path("humann_raw_pathabundance.tsv"), path("humann_raw_pathcoverage.tsv"), path("humann_raw_ko.tsv"), path("humann_raw_ec.tsv"), path("functional.provenance.json"), emit: reports
    tuple val(sample_id), path("read_genefamilies.tsv"), emit: gene_families
    tuple val(sample_id), path("read_ko.tsv"), emit: ko
    tuple val(sample_id), path("read_ec.tsv"), emit: ec
    tuple val(sample_id), path("read_pathabundance.tsv"), emit: pathway_abundance
    tuple val(sample_id), path("read_pathcoverage.tsv"), emit: pathway_coverage
    tuple val(sample_id), path("humann_raw_genefamilies.tsv"), path("humann_raw_pathabundance.tsv"), path("humann_raw_pathcoverage.tsv"), path("humann_raw_ko.tsv"), path("humann_raw_ec.tsv"), emit: raw_humann
    tuple val(sample_id), path("functional.validation.json"), emit: validation
    tuple val(sample_id), path("functional.provenance.json"), emit: provenance
    script:
    """
    ${projectDir}/bin/core/functional_annotation.sh --sample ${sample_id} --r1 ${read1} --r2 ${read2} --database-manifest ${database_manifest} --outdir . --threads ${task.cpus} --task-id ${params.task_id} --state-root ${params.outdir}
    """
}
