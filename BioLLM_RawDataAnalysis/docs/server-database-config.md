# Server database configuration

The V1 FastAPI launcher validates the server database registry before Uvicorn
starts. The default registry is `config/database-registry.local.json`, profile
`server-v1`. A resolved, timestamped manifest is written to
`runtime/config/database.resolved.json` and exported as
`BIOLLM_DATABASE_MANIFEST`.

Validation checks:

- every required core database entry and field;
- Kraken2 and Bracken identity consistency;
- database directories;
- representative sentinel files for each installation.

The registry `manifest_sha256` values identify a sorted top-level inventory of
file names and byte sizes. They are provenance identifiers, not full hashes of
multi-gigabyte database contents. The exact basis is recorded in each entry.

Operators may override:

- `BIOLLM_DATABASE_REGISTRY`;
- `BIOLLM_DATABASE_PROFILE`;
- `BIOLLM_DATABASE_MANIFEST` with an already resolved manifest.

An explicit resolved manifest must exist as a regular readable file. The
default generated manifest remains the safer path because it performs the
preflight on every backend start.

When a resolved manifest is configured, task requests cannot replace the
Kraken2, HUMAnN, MetaPhlAn, or host-reference paths with per-task values.
Matching paths are recorded for provenance; mismatched paths are rejected
before a task is created. Reference changes therefore require an operator-level
configuration review and backend restart.

The `server-v1` profile intentionally configures the core read-analysis
databases only. MAG classification/function database entries are not yet
declared; enabling MAG validation through `workflow/run_pipeline.sh
--enable-mags` will fail closed until those entries are reviewed and added.
