nextflow.enable.dsl=2

params.qc_table = null
params.outdir = "results/qc_visualization"

process QC_VISUALIZATION {
    tag "qc:${qc_table.baseName}"
    publishDir path: params.outdir, mode: "copy", overwrite: true

    input:
    path qc_table

    output:
    path "figures", emit: figures
    path "figure.data.tsv", emit: figure_data
    path "figure.provenance.json", emit: provenance
    path "qc_visualization.summary.json", emit: summary
    path "qc_visualization.log", emit: log

    script:
    """
    python3 "${projectDir}/bin/visualization/qc_plots.py" \
      --input "${qc_table}" \
      --output-dir .
    """
}

workflow {
    if (!params.qc_table) {
        error "--qc_table is required; provide a standardized QC TSV or JSON"
    }
    QC_VISUALIZATION(file(params.qc_table, checkIfExists: true))
}
