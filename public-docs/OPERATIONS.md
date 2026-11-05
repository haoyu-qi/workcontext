# WorkContext service bundle

Roles: MacBook gathers local Agent work records; Mini provides task context, coordination and memory-related query surfaces; any compatible registered node can execute. Windows support is optional and is not required for the two-Mac workflow.

## Install and check

```sh
python -m pip install ./workcontext_services-0.4.0-py3-none-any.whl
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
workcontext serve memory --config state/memory.json
workcontext relay --config state/relay.json
workcontext mcp memory --config state/memory-client.json
workcontext worker --config state/worker.json
workcontext mcp context --config state/context-mcp.json
workcontext mcp execution --config state/execution-mcp.json
```

Install the unified package into a new environment; existing device services are not automatically replaced.

## Browser console

Copy `config-examples/web.json` into a private directory, set the private
`auth.database` and `auth.credentials` paths, and run
`workcontext serve web --config /absolute/private/web.json`, then open
`http://127.0.0.1:43150`. The real-memory panel accepts a memory owner/reader
token or the complete token JSON. It provides source/agent counts, keyword
search, full records and links from explicit notes to their cited sources.
For ordinary browser access, configure `auth.credentials.memory` with a dedicated
read-only memory credential and optionally `auth.credentials.context` with the
P2 reader credential. These files remain on the service host. Their upstream
roles, grants and expiry still apply. The browser session cannot access memory
writes, worker registration or execution submission.

Generate an initial pairing code **on the service host**, with access to its
private configuration:

```sh
workcontext credentials pair --config /absolute/private/web.json
```

Enter the code in **首次配对码**. The code expires after five minutes, works once,
and generating another code invalidates the previous unused one. Pairing is
limited to ten failed attempts per minute. The browser receives a host-only
HttpOnly, SameSite=Strict cookie, additionally Secure on configured HTTPS
origins. No upstream token is returned to the browser. Refreshing reconnects
automatically until the session expires (eight hours by default, configurable
with `session_seconds` between 300 and 86400). Sessions survive service restarts;
only their hashes and upstream credential fingerprints are stored in SQLite.

**退出登录** revokes the current session on the server and clears its cookie and
page data. Expired sessions require a fresh pairing code. Rotating a configured
credential invalidates existing sessions. Backend policy revocation remains
effective on every request. To revoke all browser sessions and pending codes:

```sh
workcontext credentials revoke-sessions --config /absolute/private/web.json
```

The server checks configured Host/Origin and same-origin POSTs for cookie-authenticated
operations. The pairing and logout endpoints also require an Origin header.
Configure the exact HTTPS origin when placing the console behind a private
reverse proxy. Pairing is an owner-admin flow, not a multi-user identity provider.

For compatibility with deployments without pairing, export a single connection file
on the owning device:

```sh
workcontext credentials export --output "$HOME/Downloads/workcontext-connection.json"
```

The command reads existing memory and P2 credentials from their standard private
deployment paths. It accepts `--memory-token-file` and `--context-token-file` for
other installations. It exports only available credentials, rejects expired
credentials, creates a private file and refuses to overwrite an existing file.
The command never prints token values. Treat the output as a private credential.

Expand **兼容：导入原有连接文件** to connect both services once, or use each service's JSON
import button for its original token file. The browser reads the selected file
locally and sends only the relevant token to each configured service. It does
not store the token in browser storage; refreshing requires another import.

`memory_url` accepts an explicitly configured HTTPS memory endpoint or a local
service on port 43170/43172. Optional `memory_fallback_urls` supplies up to two
alternate transports for the same service. Authentication failures never fall
back. A remote deployment must include its exact browser origin in `origins`.
The facade binds only to loopback and preserves upstream authorization.

For offline access to records already collected on the current device, add
`local_sources` with `database` pointing to that device's relay `outbox.sqlite`
and `token_file` pointing to its private owner/reader token JSON (including
`expires_at`). This optional source opens SQLite in read-only mode, excludes
blocked records, validates the supplied token and expiry, and shows pending
versus acknowledged delivery. Select the source explicitly in the browser;
local records do not stand in for Mini's explicit long-term notes. No extra
source scanning, upload, or memory writes are triggered by this panel.

For the historical P2 panels, additionally configure `upstream` as
`http://127.0.0.1:43140` and `inventory_file` as an absolute inventory JSON path.
P2 requires its own reader token and continues to contain synthetic data.
`execution_url: http://127.0.0.1:43160` enables execution status. The health
banner reports each configured service independently; unavailable services do
not become simulated successes. This console exposes memory reads only.

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

Changed files are rescanned with logical event deduplication; unchanged files are skipped. Compressed snapshots may require full decompression. Unsupported/corrupt inputs are reported. `--max-records N` provides a bounded local trial. The independent memory Relay can upload approved work records to Mini with durable acknowledgements. Native Hermes recall/capture and explicit stable notes are described in [long-term memory](LONG-TERM-MEMORY.md); a successful local collection alone does not prove delivery or fact validation.

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
