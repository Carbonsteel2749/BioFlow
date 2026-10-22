process READS_PLOTS {
    tag "reads-qc-cohort"
    input:
    path qc_metrics, stageAs: 'inputs/*/*'
    output:
    path "reads_plots/*", optional: true, emit: plots
    path "reads_plots/reads_plots.provenance.json", emit: provenance
    script:
    """
    python3 "${projectDir}/bin/visualization/reads_plots.py" --metrics-dir inputs --outdir reads_plots
    """
}
