# Metagenomics V1 Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-server, auditable V1 web application that runs a fixed metagenomic FASTQ workflow and reports progress, results, and bounded LLM-assisted operational diagnostics.

**Architecture:** A React/Vite client talks to a FastAPI API. The API persists task state in SQLite and launches a pinned Nextflow DSL2 workflow in a unique task directory. The local `qwen3:14b` Ollama model receives only redacted failure summaries through a tool gateway that permits a fixed retry action and never changes biological methods without explicit approval.

**Tech Stack:** Python 3.11, FastAPI, SQLModel, SQLite, React, Vite, TypeScript, Nextflow DSL2, Conda/Apptainer, FastQC, fastp, Bowtie2 or KneadData, Kraken2, Bracken, HUMAnN, MultiQC, Ollama.

## Global Constraints

- Accept only de-identified paired-end FASTQ files and a minimal `sample_id,read1,read2` sample sheet.
- Keep raw data, full logs, and model inference on the hospital server.
- Pin and record every tool, database, reference genome, workflow, and parameter version.
- Use the preinstalled local `qwen3:14b` Ollama model for diagnostics; do not allow model-initiated changes to workflow source, tool version, reference database, or biological parameters.
- Use `nextflow run` with per-task work and output directories; never execute shell text supplied by a browser user.
- Do not claim clinical validity; label all outputs as research analysis results.

---

## Planned Project Structure

```text
BioLLM_RawDataAnalysis/
  README.md
  .gitignore
  config/
    profiles/local.config
    resources.yaml
  backend/
    app/main.py
    app/models.py
    app/schemas.py
    app/services/tasks.py
    app/services/progress.py
    app/services/redaction.py
    tests/test_tasks.py
    tests/test_redaction.py
  frontend/
    src/pages/TaskList.tsx
    src/pages/TaskDetail.tsx
    src/api/tasks.ts
  workflow/
    main.nf
    nextflow.config
    modules/validate.nf
    modules/fastqc.nf
    modules/fastp.nf
    modules/host_depletion.nf
    modules/taxonomy.nf
    modules/function.nf
    modules/report.nf
    assets/params.schema.json
  scripts/
    create_demo_dataset_manifest.py
    verify_installation.sh
  docs/
    data-contract.md
    operations-runbook.md
    llm-guardrails.md
```

### Task 1: Project baseline and data contract

**Owner:** ????? + ?? A??????

**Files:**
- Create: `README.md`, `.gitignore`, `docs/data-contract.md`, `config/resources.yaml`, `scripts/verify_installation.sh`

**Produces:** An agreed input contract and one de-identified public 2?4 sample demo manifest.

- [ ] Define the exact sample-sheet columns `sample_id,read1,read2` and reject duplicate sample IDs, missing mates, non-`.fastq.gz` files, and paths outside the approved input root.
- [ ] Record reference genome build, Kraken2/Bracken database release, HUMAnN database release, and resource limits in `config/resources.yaml`.
- [ ] Add `scripts/verify_installation.sh` checks for `nextflow`, `fastqc`, `fastp`, `bowtie2`, `kraken2`, `bracken`, `humann`, `multiqc`, and `ollama`; each missing dependency exits non-zero with its name.
- [ ] Run the checker on the server and attach its dated output to `docs/operations-runbook.md`.
- [ ] Acceptance: a reviewer can validate the demo manifest without seeing patient data.

### Task 2: Reproducible Nextflow skeleton and validation stage

**Owner:** ?? B?????

**Files:**
- Create: `workflow/main.nf`, `workflow/nextflow.config`, `workflow/modules/validate.nf`, `workflow/assets/params.schema.json`
- Test: `workflow/tests/validate.nf.test`

**Interfaces:**
- Consumes: `--input_manifest`, `--outdir`, `--task_id`, `--reference_config`.
- Produces: `outputs/<task_id>/manifest.validated.csv` and `logs/<task_id>/validate.json`.

- [ ] Write a failing nf-test case with a missing R2 path and assert that the process exits non-zero with `missing_mate`.
- [ ] Implement `VALIDATE_INPUTS` to parse the CSV, resolve each path under the configured input root, and emit structured JSON containing the sample count and rejection reason.
- [ ] Run `nf-test test workflow/tests/validate.nf.test` and require PASS.
- [ ] Add Nextflow `trace`, `timeline`, `report`, and DAG outputs under the task output directory.
- [ ] Acceptance: invalid input ends in `failed` before any bioinformatics tool executes.

### Task 3: Quality control, trimming, and host-depletion modules

**Owner:** ?? B?????; reviewer: ?

**Files:**
- Create: `workflow/modules/fastqc.nf`, `workflow/modules/fastp.nf`, `workflow/modules/host_depletion.nf`
- Modify: `workflow/main.nf`
- Test: `workflow/tests/preprocess.nf.test`

**Interfaces:**
- Consumes: validated paired FASTQ tuple `(sample_id, r1, r2)`.
- Produces: raw FastQC HTML/ZIP, `fastp.json`, cleaned FASTQ, host-depleted FASTQ, and per-sample read-count JSON.

- [ ] Write a test fixture with two tiny paired FASTQ files and assert that `fastp.json` and host-depleted R1/R2 files exist.
- [ ] Implement FastQC before fastp, preserving raw reports.
- [ ] Implement fastp with parameters only from a version-controlled config file; export JSON and HTML reports.
- [ ] Implement Bowtie2 host removal using a configured GRCh38 index and write retained/removed read counts.
- [ ] Fail the step if output mates are inconsistent or host-depleted output is empty; include the sample ID and tool exit code in JSON.
- [ ] Acceptance: the stage emits all declared artifacts and records tool versions/parameters.

### Task 4: Taxonomic and functional annotation modules

**Owner:** ?? C????????

**Files:**
- Create: `workflow/modules/taxonomy.nf`, `workflow/modules/function.nf`, `docs/database-registry.md`
- Modify: `workflow/main.nf`, `config/resources.yaml`
- Test: `workflow/tests/annotation.nf.test`

**Interfaces:**
- Consumes: host-depleted paired FASTQ and pinned database paths.
- Produces: `taxonomy/kraken.report`, `taxonomy/bracken_species.tsv`, `function/pathabundance.tsv`, `function/genefamilies.tsv`, and database provenance JSON.

- [ ] Define database identifiers, absolute read-only locations, checksums, and releases in `docs/database-registry.md`.
- [ ] Write a test that uses a tiny fixture or mocked commands to assert expected output file names and database provenance.
- [ ] Implement Kraken2 followed by Bracken at a documented read-length setting.
- [ ] Implement HUMAnN using the pinned nucleotide, protein, and utility databases; preserve unstratified and stratified outputs.
- [ ] Require explicit failure when a database path or index is missing; never download a database during a patient-data run.
- [ ] Acceptance: every annotation output names its database release and can be traced to the input sample.

### Task 5: Result packaging and report stage

**Owner:** ?? D????????

**Files:**
- Create: `workflow/modules/report.nf`, `docs/result-catalog.md`
- Modify: `workflow/main.nf`
- Test: `workflow/tests/report.nf.test`

**Interfaces:**
- Consumes: QC, trimming, host-depletion, taxonomy, functional-annotation artifacts.
- Produces: `report/multiqc_report.html`, `tables/*.tsv`, `provenance/run.json`, and `deliverables/<task_id>.tar.gz`.

- [ ] Write a test that asserts the archive includes MultiQC, a read-count table, species abundance, pathway abundance, parameters, versions, and checksums.
- [ ] Run MultiQC on all eligible reports and render a plain-language research-only summary from template values, not model prose.
- [ ] Produce a machine-readable `run.json` with task ID, input checksums, Nextflow run name, workflow revision, tool versions, database releases, timestamps, and final status.
- [ ] Package only result artifacts; exclude raw reads and Nextflow work directories.
- [ ] Acceptance: a user can download one archive and reproduce the provenance of each table.

### Task 6: Backend task orchestration and progress API

**Owner:** ??????? + ?? A??????

**Files:**
- Create: `backend/app/main.py`, `backend/app/models.py`, `backend/app/schemas.py`, `backend/app/services/tasks.py`, `backend/app/services/progress.py`, `backend/tests/test_tasks.py`

**Interfaces:**
- `POST /api/tasks` accepts `{manifest_path: string}` and returns `{id: string, status: "queued"}`.
- `GET /api/tasks/{id}` returns task and seven ordered step records.
- `GET /api/tasks/{id}/logs?step=<step>` returns a redacted text excerpt.
- `POST /api/tasks/{id}/retry` only accepts a failed step with a matching approved retry policy.

- [ ] Write API tests for path traversal rejection, task creation, ordered progress, and retry rejection for a successful task.
- [ ] Implement SQLite `Task`, `Step`, and `Alert` models with UUID primary keys and timestamped state transitions.
- [ ] Launch Nextflow through an argument array, not shell concatenation; assign `work/<uuid>` and `outputs/<uuid>`.
- [ ] Parse trace/timeline artifacts to update step states; use ?progress unavailable? where a percentage is not measurable.
- [ ] Acceptance: restarting the API does not lose existing task and step states.

### Task 7: Web interface for submission, progress, logs, and results

**Owner:** ?? A????; reviewer: ?

**Files:**
- Create: `frontend/src/api/tasks.ts`, `frontend/src/pages/TaskList.tsx`, `frontend/src/pages/TaskDetail.tsx`
- Test: `frontend/src/pages/TaskDetail.test.tsx`

**Interfaces:**
- Uses the Task API from Task 6 without direct filesystem or shell access.
- Renders the seven fixed stages in chronological order.

- [ ] Write a UI test with a mocked failed `host_depletion` step; assert the alert and log link render, and the downstream steps remain pending.
- [ ] Implement task creation using a server-side approved import path selector; do not upload patient files through a public browser endpoint in V1.
- [ ] Render each stage as pending, running, succeeded, failed, skipped, or paused, including timestamps and measurable progress where available.
- [ ] Add result links only when task status is `completed`; add retry only when the API reports `retry_allowed=true`.
- [ ] Acceptance: a nontechnical tester can identify the current stage, a failure reason, and the final result package without opening a terminal.

### Task 8: Bounded Ollama diagnostic assistant

**Owner:** ?? D?LLM ??? + ???????

**Files:**
- Create: `backend/app/services/redaction.py`, `backend/app/services/llm_diagnostics.py`, `docs/llm-guardrails.md`
- Test: `backend/tests/test_redaction.py`, `backend/tests/test_llm_diagnostics.py`

**Interfaces:**
- `redact_log(text: str) -> str` removes absolute paths, usernames, and configured sample identifiers.
- `diagnose_failure(step: str, log_excerpt: str) -> Diagnostic` returns `{classification, confidence, summary, recommended_action, needs_user_approval}`.

- [ ] Write redaction tests containing `/home/xh/`, a username, and sample IDs; assert no supplied sensitive token remains.
- [ ] Implement a JSON-only Ollama prompt that requests classifications from `transient_resource`, `missing_dependency`, `input_validation`, `tool_failure`, and `unknown`.
- [ ] Permit automatic retry only for idempotent, documented `transient_resource` cases with confidence at least 0.90 and at most one retry.
- [ ] For every other class, create an alert containing the redacted diagnosis and require user action; never patch source code or alter scientific parameters automatically.
- [ ] Acceptance: unknown errors pause the task and are surfaced to the user rather than being repeatedly retried.

### Task 9: Demonstration, security review, and handoff

**Owner:** ??? coordinator: ?

**Files:**
- Create: `docs/operations-runbook.md`, `docs/demo-acceptance.md`
- Modify: `README.md`

- [ ] Run one successful 2?4 sample public, de-identified demo task and capture task ID, elapsed time, resource use, and output inventory.
- [ ] Trigger one controlled invalid-manifest failure and one controlled missing-database failure; confirm alert behavior and absence of unsafe automatic changes.
- [ ] Verify the result archive has no raw reads, work directory, username, or absolute input paths.
- [ ] Conduct a team review against every V1 acceptance criterion in `docs/superpowers/specs/2026-07-18-metagenomics-v1-design.md`.
- [ ] Initialize Git before implementation begins, commit reviewed documentation and source changes, and tag the validated demo as `v0.1-demo`.
- [ ] Acceptance: all five members can reproduce the demo following only the runbook.

## Plan Self-Review

- Spec coverage: Tasks 1?5 cover the analysis and result pipeline; Tasks 6?7 cover task execution and progress UI; Task 8 covers bounded model support; Task 9 covers safety and acceptance.
- Placeholder scan: no incomplete work items are used as acceptance criteria.
- Interface consistency: API task statuses and seven workflow stages use the same names across Tasks 2, 6, and 7.
