process FUNCTIONAL_PLOTS {
    tag "functional-cohort"
    input:
    path gene_families
    path ko
    path ec
    path pathway_abundance
    path pathway_coverage
    output:
    path "functional_plots/*.png", optional: true, emit: plots
    path "functional_plots/functional_plots.provenance.json", emit: provenance
    script:
    """
    # Figure semantics v2: diagnostic categories excluded; coverage kept on 0-1 scale.
    ${projectDir}/bin/visualization/functional_plots.py --gene-families ${gene_families} --ko ${ko} --ec ${ec} --pathway-abundance ${pathway_abundance} --pathway-coverage ${pathway_coverage} --outdir functional_plots --top-n ${params.functional_plot_top_n ?: 20}
    """
}
