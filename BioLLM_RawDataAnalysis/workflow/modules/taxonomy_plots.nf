process TAXONOMY_PLOTS {
    tag "taxonomy-cohort"
    cache 'deep'
    input:
    path species_abundance
    path sample_metadata
    val metadata_sha256
    path renderer_sources
    output:
    path "taxonomy_plots/*", optional: true, emit: plots
    path "taxonomy_plots/taxonomy_plots.provenance.json", emit: provenance
    script:
    def metadataArgs = sample_metadata ? "--sample-metadata \"${sample_metadata}\"" : ''
    def digestArgs = metadata_sha256 ? "--metadata-sha256 \"${metadata_sha256}\"" : ''
    def threshold = params.taxonomy_plot_low_abundance_threshold != null ? params.taxonomy_plot_low_abundance_threshold : 0.01
    def aiArgs = params.figure_ai_advice?.toString()?.toBoolean() ? '--ai-advice' : ''
    def extended = params.figure_extended_plots == null ? true : params.figure_extended_plots.toString().toBoolean()
    """
    "${projectDir}/bin/visualization/taxonomy_plots.py" --input "${species_abundance}" --outdir taxonomy_plots --top-n ${params.taxonomy_plot_top_n ?: 20} --low-abundance-threshold ${threshold} --alpha-style "${params.taxonomy_alpha_style ?: 'auto'}" --extended-plots ${extended} ${aiArgs} ${metadataArgs} ${digestArgs}
    """
}
