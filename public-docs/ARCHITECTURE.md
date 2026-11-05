# Architecture and current boundary

MacBook Agent sources (dsh, Codex, Claude Code, Hermes) feed an explicit local archive and a scoped durable relay. Mini owns the personal digital-twin main role, long-term memory, central context/query service and execution coordinator. MacBook, Mini and optional Windows workers execute registered capabilities according to actual OS, workspace and code-revision requirements. Results are stored with attempt IDs and evidence; shared skills are distributed through a versioned manifest.

The Python package contains:

- `service`, `context_events`, `context_engine`, `context_service`: the earlier synthetic-only receiver/context pipeline.
- `execution`, `execution_worker`, `execution_mcp`: independent capability dispatch and result storage.
- `source_logs`: local real-source log parsing, normalization and deduplication, including compressed records.
- `long_memory`, `memory_service`, `memory_relay`, `memory_client`: independent durable sources and notes, scoped ingestion, retryable log delivery and memory MCP access. `integrations/hermes/workcontext-memory` provides native per-turn recall and capture.
- `skill_sync`: controlled platform-aware skill copying and rollback.
- `runtime`: optional foreground process supervision.
- `web_service`, `web/`: read-only console; service credentials remain outside browser assets.

Provider settings, SOUL, native memory and tokens remain node-specific. Generic role templates are proposed starting points, not snapshots of any device's live configuration. Private inventory reports, original user SOUL, databases, machine evidence and personalized deployment archives are not published.

The older P1/P2 central endpoint remains synthetic-only; real log relay uses the separate memory service. Native Hermes recall uses bounded lexical retrieval; stable notes require explicit curation, and historical log claims are not independently verified facts. Delivery is subject to network connectivity, with pending records retained locally. Service manifests need private local configuration; cross-platform skills need explicit platform declarations. A working worker does not prove that Hermes is installed on that machine.
