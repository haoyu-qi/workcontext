# Validation and provenance

Version 0.3.0 packages the service code from a staged multi-device implementation and adds a unified CLI, local real-source adapters and controlled skill distribution.

Local validation performed for this release:

- Existing P2 context suite: 11 passing tests, including transport through the separately installed upstream fixture environment.
- Skill sync: install, update, local-edit conflict, checksum mismatch, platform filtering, secret-file rejection, rollback and rollback-conflict tests.
- Source adapters: JSONL deduplication and content upgrade, real Zstandard decompression, read-only Hermes SQLite, incomplete-record recovery.
- On the owning MacBook, five records each from dsh, Codex, Claude Code and Hermes were parsed successfully into a private metadata-only sample archive. Samples and original histories are excluded from publication.
- The built wheel was installed into an isolated target, imported outside the repository, and checked for packaged migration SQL and Web assets. A real context database migration succeeded from the installed wheel.
- Earlier execution validation used physical Mini, MacBook and Windows workers, including native Windows checks and a configured Hermes client submitting and reading an execution result. Machine addresses, credential-bearing packages and private run logs are not shipped.

Windows Hermes configuration and skill deployment are deferred at the user's request. Public CI checks portable code on hosted Windows/macOS/Linux; it does not imply deployment to a user's Windows machine.

Publication uses an explicit Git file set and a credential/path scan before constructing the source bundle. Private original SOUL and live model/client configurations remain outside this repository. Generic role templates are examples, not live identity dumps.
