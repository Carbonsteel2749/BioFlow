# Frontend integration contract

The current frontend remains compatible with the V1 task API. The features below are capability-gated and are only enabled when the backend explicitly advertises support.

## Capability flags

`GET /api/capabilities` keeps the existing fields and may add:

```json
{
  "file_uploads": true,
  "task_cancellation": true,
  "result_preview": true
}
```

Missing flags are treated as `false`. The UI displays an actionable unavailable message instead of assuming support.

## FASTQ upload

`POST /api/uploads/files` uses `multipart/form-data` with one `file` field. It returns:

```json
{
  "id": "opaque-upload-id",
  "original_name": "S01_R1.fastq.gz",
  "size": 123456,
  "checksum": "sha256:...",
  "status": "validated"
}
```

The browser uploads files independently so each file has real transfer progress and an isolated error state.

`POST /api/uploads/manifests` accepts only opaque upload IDs:

```json
{
  "files": [
    {
      "sample_id": "S01",
      "read1_upload_id": "upload-r1",
      "read2_upload_id": "upload-r2"
    }
  ]
}
```

It returns `{ "manifest_path": "server-managed-path", "sample_count": 1 }`. The path is passed directly to the existing task creation API and is never editable or displayed to users.

## Cancellation

`POST /api/tasks/{task_id}/cancel` returns the updated task. The operation must be idempotent and safely stop the tracked Nextflow process before marking the task `cancelled`.

## Result preview

`GET /api/tasks/{task_id}/preview` returns bounded, redacted preview data:

```json
{
  "multiqc_url": "/api/tasks/<id>/reports/multiqc",
  "qc_summary": [
    { "sample_id": "S01", "raw_reads": 10000, "clean_reads": 8200, "host_removed_pct": 3.25 }
  ],
  "taxonomy_top": [
    { "name": "Bacteroides vulgatus", "abundance": 24.5 }
  ],
  "ko": { "columns": ["KO", "abundance"], "rows": [["K00001", 12.2]], "total_rows": 100 },
  "ec": { "columns": ["EC", "abundance"], "rows": [["1.1.1.1", 8.1]], "total_rows": 80 },
  "pathways": { "columns": ["pathway", "abundance"], "rows": [["PWY-001", 4.2]], "total_rows": 40 }
}
```

`GET /api/tasks/{task_id}/reports/multiqc` serves the report inline with a restrictive Content Security Policy. A missing report or preview returns `404` with a safe `detail` message.

Preview endpoints must never expose raw reads, absolute server paths, patient identifiers, or unbounded result files.
