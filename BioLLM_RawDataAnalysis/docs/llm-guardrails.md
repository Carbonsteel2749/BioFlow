# Local diagnostic assistant guardrails

The V1 diagnostic assistant uses the local Ollama model configured by
`BIOLLM_OLLAMA_MODEL` (default `qwen3:14b`). No workflow log is sent outside
the local server.

## Data boundary

Before model invocation, credentials, absolute paths, usernames, IP addresses,
email addresses, and configured sample identifiers are redacted. Only a
bounded tail excerpt, failed step, exit code, and retry count are sent to the
local model.

Credential redaction covers JSON fields, command-line options, quoted
assignments, authorization headers, and URI user information. Malformed or
otherwise unusable model responses are retained up to 20,000 characters,
redacted again, and written to the audit trail for investigation.

## Capability boundary

The model returns JSON classification, a user-facing summary, possible causes,
suggested actions, confidence, and an approval flag. It cannot execute commands
and cannot modify workflow source, tool versions, reference databases, host
references, resources, input data, or scientific parameters. Its classification
is advisory; deterministic server policy makes the final decision.

Automatic retry requires every condition below:

1. Classification is one of `transient_process`, `temporary_file`, or
   `controlled_runtime`.
2. Confidence is at least the configured threshold (default 0.90).
3. The failed step is on the idempotent allowlist.
4. Exit code 75 or a narrow deterministic log signature independently confirms
   a safe transient process, transport, or temporary-file failure.
5. The task has not previously been retried.
6. `BIOLLM_AUTO_RETRY=1` is explicitly configured.
7. A local, de-identified `BIOLLM_RETRY_VALIDATION_MANIFEST` completes
   successfully before the formal task is resumed.

OOM/exit 137, SIGTERM/exit 143, CPU/memory/disk/quota/file-descriptor
exhaustion, missing databases, damaged inputs, and biological-threshold
failures always pause and notify the user, even if the model calls them
transient. Missing dependencies, cancelled work, low confidence, malformed
model output, and unknown errors also require user action.

The one permitted retry keeps the original manifest, parameters, output
directory, work directory, source, databases, resources, and scientific
thresholds unchanged. The server only adds `nextflow -resume`; it does not
delete files or apply model-proposed commands.

## Audit trail

Each task writes a mode-0600 JSONL audit under the server state root at
`diagnostics/<task-id>.jsonl`. Events record the redacted model raw response,
parsed diagnosis, deterministic policy decision, effective final decision,
modification list, retry count, validation result, retry authorization, actual
retry start, and final retry result. An allowed policy decision remains a
non-allowed `validation_pending` final decision until validation succeeds.
The retry-start event is written only after the task state has been atomically
reset for the one permitted retry. Audit strings are redacted again before
persistence and audit paths reject symlinks and unsafe task identifiers.
