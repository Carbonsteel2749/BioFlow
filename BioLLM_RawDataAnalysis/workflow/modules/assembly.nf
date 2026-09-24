process ASSEMBLY {
    tag "coassembly"
    cpus params.mag_threads
    publishDir "${params.outdir}/mag/assembly", mode: 'copy', overwrite: true
    input:
    path reads
    output:
    tuple path("assembly/final_assembly.fasta"), path(reads), emit: mag_inputs
    path "assembly/assembly_report.html", emit: reports
    script:
    def r1Files = reads.findAll { it.name.contains('.R1.') }
    def r2Files = reads.findAll { it.name.contains('.R2.') }
    """
    printf '%s\n' ${r1Files.join(' ')} | tr ' ' '\n' > r1.list
    printf '%s\n' ${r2Files.join(' ')} | tr ' ' '\n' > r2.list
    mkdir -p assembly
    ${projectDir}/bin/mag/assembly.sh --r1-list r1.list --r2-list r2.list --outdir assembly \
      --threads ${task.cpus} --memory-gb ${params.mag_memory_gb} --assembler ${params.assembler} \
      --task-id ${params.task_id} --state-root ${params.outdir}
    """
}
