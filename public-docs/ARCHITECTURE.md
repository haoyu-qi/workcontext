# Architecture and current boundary

MacBook Agent sources (dsh, Codex, Claude Code, Hermes) feed an explicit local archive. Mini owns the central context/query service and execution coordinator. MacBook, Mini and optional Windows workers execute registered capabilities according to actual OS, workspace and code-revision requirements. Results are stored with attempt IDs and evidence; shared skills are distributed through a versioned manifest.

The Python package contains:

- `service`, `context_events`, `context_engine`, `context_service`: the earlier synthetic-only receiver/context pipeline.
- `execution`, `execution_worker`, `execution_mcp`: independent capability dispatch and result storage.
- `source_logs`: local real-source log parsing, normalization and deduplication, including compressed records.
- `skill_sync`: controlled platform-aware skill copying and rollback.
- `runtime`: optional foreground process supervision.
- `web_service`, `web/`: read-only console; service credentials remain outside browser assets.

Provider settings, SOUL, native memory and tokens remain node-specific. Generic role templates are proposed starting points, not snapshots of any device's live configuration. Private inventory reports, original user SOUL, databases, machine evidence and personalized deployment archives are not published.

Current limitations are deliberate and visible: real-source local archiving is not yet wired into the old synthetic-only central ingestion endpoint; automatic long-term memory consolidation is not included; service manifests need private local configuration; cross-platform skills need explicit platform declarations. A working worker does not prove that Hermes is installed on that machine.
