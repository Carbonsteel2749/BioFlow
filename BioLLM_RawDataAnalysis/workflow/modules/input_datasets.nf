process INPUT_DATASET {
    tag "${node_id}"
    cache 'deep'
    input:
    tuple val(node_id), path(source_files, stageAs: 'source????/*')
    path binding_manifest, stageAs: 'bindings.json'
    output:
    path 'dataset', emit: dataset
    script:
    // Nextflow unwraps singleton path collections; Path.collect iterates path
    // components, not files. Preserve one-file contig/MAG/matrix datasets.
    def files = source_files instanceof List ? source_files : [source_files]
    def paths = groovy.json.JsonOutput.toJson(files.collect { it.toString() }).bytes.encodeBase64().toString()
    """
    python3 "${projectDir}/bin/core/input_datasets.py" --manifest "${binding_manifest}" --node "${node_id}" --paths-base64 '${paths}' --out dataset
    """
}

process MATRIX_SUMMARY {
    cache 'deep'
    publishDir path: { "${params.outdir}/${task.ext.bioflow_output_subdir}/matrix_summary" }, mode: 'copy'
    input:
    path dataset
    output:
    path 'summary', emit: report
    script:
    """
    export OMP_NUM_THREADS=${task.cpus} MKL_NUM_THREADS=${task.cpus} OPENBLAS_NUM_THREADS=${task.cpus}
    python3 "${projectDir}/bin/core/input_datasets.py" --matrix "${dataset}" --out summary
    """
}

process CONNECTED_READS_MANIFEST {
    cache 'deep'
    input:
    tuple val(samples), path(reads, stageAs: 'reads????/*')
    output:
    path 'samples.csv', emit: manifest
    tuple path('samples.csv'), path(reads), emit: inputs
    script:
    def ids = groovy.json.JsonOutput.toJson(samples).bytes.encodeBase64().toString()
    def paths = groovy.json.JsonOutput.toJson(reads.collect { it.toString() }).bytes.encodeBase64().toString()
    """
    python3 "${projectDir}/bin/core/input_datasets.py" --samples-base64 '${ids}' --paths-base64 '${paths}' --out samples.csv
    """
}
