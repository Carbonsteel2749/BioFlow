def taxonomyFigurePython() {
    System.getenv('BIOLLM_TAXONOMY_FIGURE_PYTHON') ?: 'python3'
}

def taxonomyFigurePin(workflowRoot) {
    def command = [taxonomyFigurePython(), "${workflowRoot}/bin/visualization/shared_taxonomy.py".toString(), '--fingerprint']
    def builder = new ProcessBuilder(command)
    builder.environment().put('PYTHONDONTWRITEBYTECODE', '1')
    def process = builder.start()
    def stdout = new StringBuffer()
    def stderr = new StringBuffer()
    process.waitForProcessOutput(stdout, stderr)
    def pin = stdout.toString().trim()
    if (process.exitValue() != 0 || !(pin == 'standalone' || pin ==~ /[0-9a-f]{64}/)) {
        throw new IllegalStateException("Cannot pin shared taxonomy renderer sources: ${stderr.toString().trim()}")
    }
    return pin
}

process TAXONOMY_PLOTS {
    tag "taxonomy-cohort"
    cache 'deep'
    input:
    path species_abundance
    path sample_metadata
    val metadata_sha256
    path renderer_sources
    val shared_taxonomy_pin
    output:
    path "taxonomy_plots/*", optional: true, emit: plots
    path "taxonomy_plots/taxonomy_plots.provenance.json", emit: provenance
    script:
    def metadataArgs = sample_metadata ? "--sample-metadata \"${sample_metadata}\"" : ''
    def digestArgs = metadata_sha256 ? "--metadata-sha256 \"${metadata_sha256}\"" : ''
    def threshold = params.taxonomy_plot_low_abundance_threshold != null ? params.taxonomy_plot_low_abundance_threshold : 0.01
    def aiArgs = params.figure_ai_advice?.toString()?.toBoolean() ? '--ai-advice' : ''
    def extended = params.figure_extended_plots == null ? true : params.figure_extended_plots.toString().toBoolean()
    def taskId = (params.task_id ?: 'manual').toString().replaceAll('[^a-zA-Z0-9._-]', '_')
    def figurePython = taxonomyFigurePython()
    """
    PYTHONDONTWRITEBYTECODE=1 "${figurePython}" "${projectDir}/bin/visualization/taxonomy_plots.py" --input "${species_abundance}" --outdir taxonomy_plots --task-id "${taskId}" --shared-taxonomy-pin "${shared_taxonomy_pin}" --top-n ${params.taxonomy_plot_top_n ?: 20} --low-abundance-threshold ${threshold} --alpha-style "${params.taxonomy_alpha_style ?: 'auto'}" --extended-plots ${extended} ${aiArgs} ${metadataArgs} ${digestArgs}
    """
}
