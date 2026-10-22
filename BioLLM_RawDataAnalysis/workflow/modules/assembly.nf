process ASSEMBLY {
    tag "coassembly"
    cpus params.mag_threads
    publishDir path: { "${params.outdir}/${task.ext.bioflow_output_subdir ? task.ext.bioflow_output_subdir + '/' : ''}mag/assembly" }, mode: 'copy', overwrite: true
    input:
    path reads
    output:
    tuple path("assembly/final_assembly.fasta"), path(reads), emit: mag_inputs
    path "assembly/assembly_report.html", emit: reports
    script:
    def options = task.ext.bioflow_parameters ?: params
    def stateRoot = task.ext.bioflow_output_subdir ? "${params.outdir}/${task.ext.bioflow_output_subdir}" : params.outdir
    def scriptRoot = task.ext.bioflow_script_root ?: "${projectDir}/bin"
    def r1Files = reads.findAll { it.name.contains('.R1.') }
    def r2Files = reads.findAll { it.name.contains('.R2.') }
    """
    # bioflow-node ${task.ext.bioflow_fingerprint ?: 'standard'} repair ${task.ext.bioflow_repair_digest ?: 'original'}
    printf '%s\n' ${r1Files.join(' ')} | tr ' ' '\n' > r1.list
    printf '%s\n' ${r2Files.join(' ')} | tr ' ' '\n' > r2.list
    mkdir -p assembly
    "${scriptRoot}/mag/assembly.sh" --r1-list r1.list --r2-list r2.list --outdir assembly \
      --threads ${task.cpus} --memory-gb ${options.mag_memory_gb} --assembler ${options.assembler} \
      --task-id "${params.task_id}" --state-root "${stateRoot}"
    """
}
