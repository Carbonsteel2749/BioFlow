process TAXONOMY_PLOTS {
    tag "taxonomy-cohort"
    input:
    path species_abundance
    output:
    path "taxonomy_plots/*.png", optional: true, emit: plots
    path "taxonomy_plots/taxonomy_plots.provenance.json", emit: provenance
    script:
    """
    ${projectDir}/bin/visualization/taxonomy_plots.py --input ${species_abundance} --outdir taxonomy_plots --top-n ${params.taxonomy_plot_top_n ?: 20} --low-abundance-threshold ${params.taxonomy_plot_low_abundance_threshold ?: 0.01}
    """
}
