nextflow.enable.dsl=2

params.input_manifest = null
params.outdir = 'results'
params.task_id = 'manual'
params.database_registry = null
params.database_profile = null
params.database_manifest = null
params.enable_mags = false
params.enable_reassembly = true
params.threads = 4
params.fastp_qualified_quality_phred = 20
params.fastp_unqualified_percent_limit = 40
params.fastp_n_base_limit = 5
params.fastp_length_required = 50
params.fastp_cut_front = true
params.fastp_cut_tail = true
params.fastp_cut_window_size = 4
params.fastp_cut_mean_quality = 20
params.fastp_trim_poly_g = true
params.fastp_correction = false
params.fastp_detect_adapter_for_pe = true
params.mag_threads = 8
params.mag_memory_gb = 32
params.read_length = 150
params.host_index = ''
params.host_filter_mode = 'strict_both_unmapped'
params.host_min_retained_pairs = 0
params.host_max_removed_pct = 100
params.host_bowtie2_preset = 'very-sensitive'
params.assembler = 'megahit'
params.bin_completeness = 70
params.bin_contamination = 5
params.taxonomy_plot_top_n = 20
params.taxonomy_plot_low_abundance_threshold = 0.01
params.functional_plot_top_n = 20
params.mag_plot_top_n = 20
params.mag_plot_detection_threshold = 0

include { VALIDATE_MANIFEST } from './modules/validate'
include { FASTQC_RAW } from './modules/fastqc'
include { FASTP } from './modules/fastp'
include { HOST_DEPLETION } from './modules/host_depletion'
include { TAXONOMY } from './modules/taxonomy'
include { FUNCTIONAL_ANNOTATION } from './modules/functional_annotation'
include { TAXONOMY_PLOTS } from './modules/taxonomy_plots'
include { FUNCTIONAL_PLOTS } from './modules/functional_plots'
include { ASSEMBLY } from './modules/assembly'
include { BINNING } from './modules/binning'
include { BIN_REFINEMENT } from './modules/bin_refinement'
include { BIN_QUANTIFICATION } from './modules/bin_quantification'
include { BIN_REASSEMBLY } from './modules/bin_reassembly'
include { BIN_ANNOTATION } from './modules/bin_annotation'
include { MAG_PLOTS } from './modules/mag_plots'
include { REPORT } from './modules/report'

workflow {
    if (!params.input_manifest) {
        error "--input_manifest is required"
    }
    if (!params.database_manifest) {
        error "--database_manifest is required; run workflow/run_pipeline.sh preflight or provide a validated manifest"
    }
    database_manifest_ch = Channel.value(file(params.database_manifest, checkIfExists: true))
    input_manifest_ch = Channel.value(file(params.input_manifest, checkIfExists: true))
    VALIDATE_MANIFEST(file(params.input_manifest))
    samples = VALIDATE_MANIFEST.out.validated_manifest
        .splitCsv(header: true)
        .map { row -> tuple(row.sample_id as String, file(row.read1), file(row.read2)) }

    FASTQC_RAW(samples)
    FASTP(samples)
    HOST_DEPLETION(FASTP.out.reads)
    TAXONOMY(HOST_DEPLETION.out.reads, database_manifest_ch)
    FUNCTIONAL_ANNOTATION(HOST_DEPLETION.out.reads, database_manifest_ch)

    taxonomy_plot_input = TAXONOMY.out.species_abundance
        .map { sample_id, species_abundance -> species_abundance }
        .collectFile(name: 'species_abundance.tsv', keepHeader: true)
    TAXONOMY_PLOTS(taxonomy_plot_input)

    functional_gene_families = FUNCTIONAL_ANNOTATION.out.gene_families
        .map { sample_id, table -> table }
        .collectFile(name: 'gene_families.tsv', keepHeader: true)
    functional_ko = FUNCTIONAL_ANNOTATION.out.ko
        .map { sample_id, table -> table }
        .collectFile(name: 'ko.tsv', keepHeader: true)
    functional_ec = FUNCTIONAL_ANNOTATION.out.ec
        .map { sample_id, table -> table }
        .collectFile(name: 'ec.tsv', keepHeader: true)
    functional_pathway_abundance = FUNCTIONAL_ANNOTATION.out.pathway_abundance
        .map { sample_id, table -> table }
        .collectFile(name: 'pathway_abundance.tsv', keepHeader: true)
    functional_pathway_coverage = FUNCTIONAL_ANNOTATION.out.pathway_coverage
        .map { sample_id, table -> table }
        .collectFile(name: 'pathway_coverage.tsv', keepHeader: true)
    FUNCTIONAL_PLOTS(
        functional_gene_families,
        functional_ko,
        functional_ec,
        functional_pathway_abundance,
        functional_pathway_coverage,
    )

    report_inputs = FASTQC_RAW.out.reports
        .mix(FASTP.out.reports)
        .mix(HOST_DEPLETION.out.metrics)
        .mix(TAXONOMY.out.reports)
        .mix(FUNCTIONAL_ANNOTATION.out.reports)
        .flatMap { outputs -> outputs.drop(1) }
    // The reports bundle already includes coverage, KO and EC files. Do not
    // mix their (sample_id, path) tuples back into the path-only report input.

    if (params.enable_mags.toString().toBoolean()) {
        mag_reads = HOST_DEPLETION.out.reads.flatMap { sample_id, read1, read2 -> [read1, read2] }.collect()
        ASSEMBLY(mag_reads)
        BINNING(ASSEMBLY.out.mag_inputs)
        BIN_REFINEMENT(BINNING.out.bins)
        BIN_QUANTIFICATION(BIN_REFINEMENT.out.refined, input_manifest_ch, database_manifest_ch)
        report_inputs = report_inputs
            .mix(ASSEMBLY.out.reports)
            .mix(BIN_REFINEMENT.out.reports)
            .mix(BIN_QUANTIFICATION.out.reports)
            .mix(BIN_QUANTIFICATION.out.abundance)
        if (params.enable_reassembly.toString().toBoolean()) {
            BIN_REASSEMBLY(BIN_REFINEMENT.out.refined)
            mag_annotation_input = BIN_REASSEMBLY.out.reassembled
                .map { bins, assembly, reads -> tuple('coassembly', bins) }
            BIN_ANNOTATION(mag_annotation_input, database_manifest_ch)
            MAG_PLOTS(BIN_ANNOTATION.out.mag_taxonomy, BIN_QUANTIFICATION.out.abundance)
            report_inputs = report_inputs.mix(BIN_REASSEMBLY.out.reports)
        } else {
            mag_annotation_input = BIN_REFINEMENT.out.refined
                .map { bins, assembly, reads -> tuple('coassembly', bins) }
            BIN_ANNOTATION(mag_annotation_input, database_manifest_ch)
            MAG_PLOTS(BIN_ANNOTATION.out.mag_taxonomy, BIN_QUANTIFICATION.out.abundance)
        }
        report_inputs = report_inputs
            .mix(BIN_ANNOTATION.out.raw_taxonomy.flatMap { outputs -> outputs.drop(1) })
            .mix(BIN_ANNOTATION.out.raw_functions.flatMap { outputs -> outputs.drop(1) })
            .mix(BIN_ANNOTATION.out.mag_taxonomy.flatMap { outputs -> outputs.drop(1) })
            .mix(BIN_ANNOTATION.out.mag_functions.flatMap { outputs -> outputs.drop(1) })
            .mix(BIN_ANNOTATION.out.provenance.flatMap { outputs -> outputs.drop(1) })
    }
    report_inputs = report_inputs
        .mix(TAXONOMY_PLOTS.out.plots)
        .mix(TAXONOMY_PLOTS.out.provenance)
        .mix(FUNCTIONAL_PLOTS.out.plots)
        .mix(FUNCTIONAL_PLOTS.out.provenance)
    if (params.enable_mags.toString().toBoolean()) {
        report_inputs = report_inputs
            .mix(MAG_PLOTS.out.plots.map { cohort_id, plot -> plot })
            .mix(MAG_PLOTS.out.provenance.map { cohort_id, provenance -> provenance })
    }
    report_parameters = groovy.json.JsonOutput.toJson(params)
    report_parameters_base64 = report_parameters.bytes.encodeBase64().toString()
    REPORT(
        report_inputs.collect(),
        database_manifest_ch,
        params.input_manifest as String,
        report_parameters_base64,
        workflow.runName,
        workflow.workDir.toString()
    )
}
