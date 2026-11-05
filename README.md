# WorkContext

Agent work context, capability-based execution and controlled skill distribution across a MacBook, a Mac mini, and optional Windows workers.

- **MacBook:** local work entry point and Agent log collection, including dsh, Codex, Claude Code and Hermes.
- **Mini:** context/query services, task coordination and execution-result archive.
- **Execution:** choose a compatible registered node by OS, workspace, capability, revision, liveness and load. Windows-specific work requires Windows; other work can run on either Mac.
- **Skills:** checksum manifests, platform selection, local-edit protection, backup and rollback. Node SOUL, models, credentials and private memory remain independent.

## Quick start

```sh
python -m pip install .
workcontext selfcheck
workcontext --help
workcontext logs inventory --config config-examples/log-sources.json
workcontext skills plan --source skills --manifest skills/manifest.json --target "$HOME/.hermes/skills"
```

Python 3.9+; no third-party Python dependency is required by the core. Compressed logs need a Zstandard decoder; unsupported sources are reported.

See [operations](public-docs/OPERATIONS.md), [architecture and limitations](public-docs/ARCHITECTURE.md), and the [example role profiles](config-examples/hermes).

The console and execution services have been exercised with physical Mac and Windows workers. This repository contains portable service code and sanitized examples, **not** live keys, private SOUL snapshots, personal Agent histories or machine databases. Service manifests require local private configuration. Earlier P1/P2 ingestion remains synthetic-only; local real-source collection does not imply central memory ingestion or automatic long-term consolidation has been deployed.

## Build and test

```sh
python -m pip wheel . --no-deps --wheel-dir dist
python -m unittest discover -s tests -p test_skills.py -v
python -m unittest discover -s tests -p test_sources.py -v
python -m unittest discover -s tests -p test_execution_core.py -v
```

The integration tests for the historical AgentPulse pipeline require its separately installed upstream checkout and runtime. AgentPulse is not vendored into this package.
