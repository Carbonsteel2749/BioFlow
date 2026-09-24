process MAG_PLOTS {
    tag "${cohort_id}"
    input:
    tuple val(cohort_id), path(mag_annotation)
    path sample_mag_abundance
    output:
    tuple val(cohort_id), path("mag_plots/*.png"), optional: true, emit: plots
    tuple val(cohort_id), path("mag_plots/mag_plots.provenance.json"), emit: provenance
    script:
    """
    ${projectDir}/bin/visualization/mag_plots.py --mag-annotation ${mag_annotation} --sample-mag-abundance ${sample_mag_abundance} --outdir mag_plots --top-n ${params.mag_plot_top_n ?: 20} --detection-threshold ${params.mag_plot_detection_threshold ?: 0}
    """
}
