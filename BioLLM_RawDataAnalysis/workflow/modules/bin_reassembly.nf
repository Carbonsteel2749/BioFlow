process BIN_REASSEMBLY {
    tag "coassembly"
    cpus params.mag_threads
    publishDir "${params.outdir}/mag/bin_reassembly", mode: 'copy', overwrite: true
    input:
    tuple path(bins), path(assembly), path(reads)
    output:
    tuple path("reassembly/reassembled_bins"), path(assembly), path(reads), emit: reassembled
    path "reassembly/reassembled_bins.stats", emit: reports
    script:
    def r1Files = reads.findAll { it.name.contains('.R1.') }
    def r2Files = reads.findAll { it.name.contains('.R2.') }
    """
    printf '%s\n' ${r1Files.join(' ')} | tr ' ' '\n' > r1.list
    printf '%s\n' ${r2Files.join(' ')} | tr ' ' '\n' > r2.list
    mkdir -p reassembly
    ${projectDir}/bin/mag/bin_reassembly.sh --bins ${bins} --r1-list r1.list --r2-list r2.list \
      --outdir reassembly --threads ${task.cpus} --memory-gb ${params.mag_memory_gb} \
      --completeness ${params.bin_completeness} --contamination ${params.bin_contamination} \
      --task-id ${params.task_id} --state-root ${params.outdir}
    """
}
