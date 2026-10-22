process FUNCTIONAL_PLOTS {
    tag "functional-cohort"
    cache 'deep'
    input:
    path gene_families
    path ko
    path ec
    path pathway_abundance
    path pathway_coverage
    path sample_metadata
    val metadata_sha256
    path renderer_sources
    output:
    path "functional_plots/*", optional: true, emit: plots
    path "functional_plots/functional_plots.provenance.json", emit: provenance
    script:
    def metadataArgs = sample_metadata ? "--sample-metadata \"${sample_metadata}\"" : ''
    def digestArgs = metadata_sha256 ? "--metadata-sha256 \"${metadata_sha256}\"" : ''
    def aiArgs = params.figure_ai_advice?.toString()?.toBoolean() ? '--ai-advice' : ''
    def extended = params.figure_extended_plots == null ? true : params.figure_extended_plots.toString().toBoolean()
    """
    # Figure semantics v2: diagnostic categories excluded; coverage kept on 0-1 scale.
    "${projectDir}/bin/visualization/functional_plots.py" --gene-families "${gene_families}" --ko "${ko}" --ec "${ec}" --pathway-abundance "${pathway_abundance}" --pathway-coverage "${pathway_coverage}" --outdir functional_plots --top-n ${params.functional_plot_top_n ?: 20} --display-scale "${params.functional_plot_scale ?: 'relative'}" --extended-plots ${extended} ${aiArgs} ${metadataArgs} ${digestArgs}
    """
}
