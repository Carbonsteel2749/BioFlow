process VALIDATE_MANIFEST {
    tag "manifest:${manifest.baseName}"
    publishDir "${params.outdir}/validate", mode: 'copy', overwrite: true
    input:
    path manifest
    output:
    path "validated/manifest.validated.csv", emit: validated_manifest
    script:
    """
    mkdir -p validated
    "${projectDir}/bin/core/validate.sh" --manifest "${manifest}" --outdir validated \
      --task-id "${params.task_id}" --state-root "${params.outdir}"
    """
}
