# First-stage writing material intake

The writing branch accepts existing BioLLM archives, report directories, and
workflow task directories containing `nodes/*/tables/read_counts.tsv`. If more
than one report node is present, selection must be explicit. Package labels are
resolved read-only from `workflow_runs` or legacy `tasks`.

The materials adapter streams QC and taxonomic abundance tables and only reads
headers from the remaining tables. Gene-family matrices are not loaded into
memory. Reported inventory counts remain labelled as reported inventory, not
independently verified measurements. Each fact records its relative source file,
row/column when applicable, sample and unit. Per-sample taxonomy maxima are
descriptive selections, not group comparisons.

`POST /api/materials/check` uses the same adapter as generation. The unified
writing form first displays material availability, limitations and expandable
source facts; a second submission confirms descriptive drafting. Changed input
requires another check. Unsupported layouts explicitly retain the legacy path.

Generation stores the report in chapter metadata and passes section-specific
facts and descriptive boundaries to the model. The material path rejects numbers
absent from its catalog in strict composition. This is a numeric consistency
guard, not proof of semantic correctness: a number used in the wrong context or
an unsupported qualitative claim still requires review. Source links describe
inputs; this stage does not implement sentence-level claim-to-source alignment.

Study design, experimental methods and inferential statistics remain unconfirmed
in this first-stage adapter. It does not derive P values, group differences,
diversity, enrichment, or causality from abundance files. HTML QC reports are
distinct from standalone manuscript figures. Literature evidence integration,
design confirmation forms and the native Skills center are later stages.

Validation: `python -m pytest -q tests/test_material_intake.py tests/test_writing_binding.py`
with the existing server Python environment. Preview uses writing API port 8766
and gateway port 8806 bound to loopback, accessed through the SSH tunnel on 18806.
No original analysis files are modified.
