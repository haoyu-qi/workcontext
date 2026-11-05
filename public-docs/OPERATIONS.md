# WorkContext service bundle

Roles: MacBook gathers local Agent work records; Mini provides task context, coordination and memory-related query surfaces; any compatible registered node can execute. Windows support is optional and is not required for the two-Mac workflow.

## Install and check

```sh
python -m pip install ./workcontext_services-0.3.0-py3-none-any.whl
workcontext selfcheck
workcontext --help
```

Python 3.9+ is required; the core has no third-party Python runtime dependencies. Zstandard source logs require Python's `compression.zstd`, the `zstd` executable, or the optional `zstandard` Python module. Report missing decoders rather than silently skipping compressed work history.

## Service entry points

```sh
workcontext serve receiver --config state/receiver.json
workcontext serve context --config state/context.json
workcontext serve execution --config state/execution.json
workcontext serve web --config state/web.json
workcontext worker --config state/worker.json
workcontext mcp context --config state/context-mcp.json
workcontext mcp execution --config state/execution-mcp.json
```

Install the unified package into a new environment; existing device services are not automatically replaced.

Private configurations and keys are created and stored on the owning devices, not downloaded from this repository. Configuration JSON schemas are defined by the corresponding modules. A P1/P2 receiver and its event validation remain synthetic-only; adding log adapters does not silently make the old ingestion endpoint accept real transcripts.

`config-examples/services-mini.json` and `services-macbook.json` show a unified foreground supervisor manifest. Copy a manifest into a private profile directory, supply its private state configuration, then run:

```sh
workcontext services --config /absolute/private/profile/services.json
```

`@python` resolves to the current Python interpreter. Dependencies specify process start order, not application readiness. OS service managers remain appropriate for unattended operation. Do not run this supervisor alongside existing LaunchAgents using the same ports. This release packages existing services; it does not automatically migrate an existing installation or provision credentials.

## Local Agent logs

```sh
workcontext logs inventory --config config-examples/log-sources.json
workcontext logs collect --config config-examples/log-sources.json
workcontext logs watch --config config-examples/log-sources.json --interval 30
```

Adapters cover dsh JSONL/Zstandard, Codex JSONL/Zstandard, Claude Code JSONL, and read-only Hermes `state.db` messages. Source paths are explicit. Collection is local-only; the command does not upload records to GitHub, Mini, or a model provider. Default storage contains normalized event metadata; `--include-content` opts into scrubbed source content. Reasoning fields and recognized credential fields are removed, but pattern filtering is not a guarantee that arbitrary personal information is absent.

Changed files are rescanned with logical event deduplication; unchanged files are skipped. Compressed snapshots may require full decompression. Unsupported/corrupt inputs are reported. `--max-records N` provides a bounded local trial. Archive-to-Mini ingestion and automatic long-term memory consolidation are separate integration work, not implied by a successful local collection.

## Shared skill synchronization

```sh
workcontext skills manifest --source skills --names workcontext-operations --manifest skills/manifest.json
workcontext skills plan --source skills --manifest skills/manifest.json --target "$HOME/.hermes/skills"
workcontext skills apply --source skills --manifest skills/manifest.json --target "$HOME/.hermes/skills"
workcontext skills rollback --record /path/printed/by/apply/transaction.json
```

Use `--platform macos|windows|linux` to verify another node's selection. Synchronize only named skills. Files are checksum verified; local changes conflict; updates are backed up; rollback refuses to overwrite subsequent edits. Symbolic links, private key files, credentials and SOUL/native memory files are rejected. No periodic background deployment or automatic code execution is enabled. Changing another node's SOUL or provider is a separate operation.

The repository is the versioned skill source; use a pinned commit/release on recipients, then apply the manifest. Synchronization is explicit and repeatable. OS-specific skills can declare a `platforms` list. This release includes one portable operations skill and does not claim that every third-party skill is portable or redistributed.

## Execution semantics

The scheduler matches OS, registered capability, project revision and workspace constraints before preferences and load. Worker liveness comes from actual heartbeats. Stale running attempts enter recovery-required rather than being blindly rerun. Operators, read-only clients and workers have separate scoped credentials.

Execution results are durable queryable evidence. They are not equivalent to automatic import of all Agent histories or a rewrite of Hermes native long-term memory.
