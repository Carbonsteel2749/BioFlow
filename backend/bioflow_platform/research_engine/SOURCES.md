# YanJi computation adapter

Source: https://github.com/Jaliiina/YanJi
Upstream source revision: 2f819f7 (imported from the existing YanJi trial checkout).

The `src/yanji` directory retains upstream module names and implementation. BioFlow owns account/project authorization, immutable dataset versions, plan confirmation, queue state, results, and asset registration. Upstream databases are disposable execution contexts scoped to an operation/run.

Imported integration patches in agent.py normalize unambiguous nested program fields and request schema-constrained JSON from the configured local llama-server endpoint. No trial datasets, runtime database, model weights, environment files, or credentials are included.

Scientific dependencies are installed separately using deploy/requirements-research.txt. The upstream checkout supplied for this integration did not include a root LICENSE file; this note records provenance and does not assign a new license to upstream code.
